"""Local HTTP/SSE server for the two Tower of Hanoi comparison views."""

from __future__ import annotations

import argparse
from collections import deque
import copy
import json
import os
import queue
import random
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.parse

from .game import ASSESS_CLAIM, HanoiError, HanoiSession, MOVE_DISK, POST_CLAIM, WAIT, sanitize_event
from . import providers

MAX_BODY = 24_000
MAX_CLIENTS = 32
MAX_EVENTS = 128
MAX_DECISIONS = 128
MAX_SOLVERS = 16
DEFAULT_DURATION = 300
DEFAULT_SOLVERS = 3
DEFAULT_PORT = 5491
PARTICIPANTS = (
    "nova", "atlas", "cipher", "ember", "onyx", "viper", "zenith", "lumen",
    "quasar", "orbit", "raven", "sable", "cobalt", "indigo", "jade", "aster",
)


def _int(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise HanoiError(f"{name}_invalid")
    return value


def _float(value: object, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
        raise HanoiError(f"{name}_invalid")
    return float(value)


def _model(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    if not isinstance(value, str) or not value or len(value) > 128 or any(ch.isspace() for ch in value):
        raise HanoiError("model_invalid")
    return value


def _participants(count: int, seed: int) -> list[str]:
    names = list(PARTICIPANTS)
    random.Random(seed).shuffle(names)
    return names[:count]


class EventBus:
    """Bounded fan-out for one sanitized public state stream."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cursor = 0
        self.history: deque[tuple[int, str]] = deque(maxlen=MAX_EVENTS)
        self.subscribers: set[queue.Queue[tuple[int, str]]] = set()
        self.latest: dict[str, object] = {"type": "comparison", "status": "idle", "timer": {"status": "idle"}, "sides": {"left": {"status": "idle"}, "right": {"status": "idle"}}}

    def _encode(self, value: dict[str, object]) -> tuple[int, str]:
        self.cursor += 1
        return self.cursor, json.dumps({**value, "cursor": self.cursor}, separators=(",", ":"), sort_keys=True)

    def publish(self, value: dict[str, object]) -> None:
        with self.lock:
            self.latest = value
            item = self._encode(value)
            self.history.append(item)
            for subscriber in tuple(self.subscribers):
                try:
                    subscriber.put_nowait(item)
                except queue.Full:
                    try:
                        subscriber.get_nowait()
                        subscriber.put_nowait(item)
                    except queue.Empty:
                        pass

    def subscribe(self, last_event_id: str | None) -> queue.Queue[tuple[int, str]] | None:
        with self.lock:
            if len(self.subscribers) >= MAX_CLIENTS:
                return None
            try:
                cursor = int(last_event_id) if last_event_id else None
            except ValueError:
                cursor = None
            subscriber: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=MAX_EVENTS)
            stale = cursor is None or not self.history or cursor > self.cursor or cursor < self.history[0][0] - 1
            replay = [self._encode(self.latest)] if stale else [item for item in self.history if item[0] > cursor]
            for item in replay:
                subscriber.put_nowait(item)
            self.subscribers.add(subscriber)
            return subscriber

    def unsubscribe(self, subscriber: queue.Queue[tuple[int, str]]) -> None:
        with self.lock:
            self.subscribers.discard(subscriber)


class LaneWorker:
    def __init__(self, run: "ComparisonRun", side: str, session: HanoiSession, engine: str, model: str, judge_model: str, threshold: float) -> None:
        self.run = run
        self.side = side
        self.session = session
        self.engine = engine
        self.model = model
        self.judge_model = judge_model
        self.threshold = threshold
        self.status = "starting"
        self.events: deque[dict[str, object]] = deque(maxlen=MAX_EVENTS)
        self.decisions: deque[dict[str, object]] = deque(maxlen=MAX_DECISIONS)
        self.full_events: deque[dict[str, object]] = deque(maxlen=2000)
        self.paused = threading.Event()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"hanoi-{side}")
        self._last_move_choice: str | None = None

    def start(self) -> None:
        self.status = "live"
        self.thread.start()

    def _record(self, event: dict[str, object]) -> None:
        safe = sanitize_event(event)
        if safe is None:
            return
        self.events.append(safe)
        self.full_events.append(safe)
        self.run.publish()

    def _trace(self, trace: dict[str, object]) -> None:
        safe = providers.sanitize_metadata(trace)
        if not isinstance(safe, dict):
            return
        safe["kind"] = "participant_decision"
        safe["actor"] = str(trace.get("actor", "participant"))[:64]
        safe["observed_session_seq"] = trace.get("observed_session_seq", 0)
        self.decisions.append(safe)
        self.events.append(safe)
        self.full_events.append(safe)
        self.run.publish()

    def _choice_offline(self, state: dict[str, object], choices: dict[str, str], actor: str) -> tuple[str, dict[str, object]]:
        completion = state.get("completion") if isinstance(state.get("completion"), dict) else {}
        if completion.get("claim_open") and f"assess:endorse" in choices:
            return "assess:endorse", {"offline": True, "confidence": 0.6, "rationale": "bounded local fallback reviewed the open claim"}
        if state.get("target_reached") and POST_CLAIM in choices:
            return POST_CLAIM, {"offline": True, "confidence": 0.9, "rationale": "the objective board is assembled and needs a participant claim"}
        move_labels = [label for label in choices if label.startswith("move:")]
        if self._last_move_choice:
            reversed_label = self._last_move_choice.replace("move:", "move:", 1)
            try:
                _, rods, disk = reversed_label.split(":")
                source, destination = rods.split(">")
                reverse = f"move:{destination}>{source}:{disk}"
                preferred = [label for label in move_labels if label != reverse]
                if preferred:
                    move_labels = preferred
            except ValueError:
                pass
        if move_labels:
            return move_labels[0], {"offline": True, "confidence": 0.2, "rationale": "provider key unavailable; selected the first legal candidate"}
        return WAIT, {"offline": True, "confidence": 0.0, "rationale": "no actionable candidate was available"}

    def _provider_choice(self, state: dict[str, object], choices: dict[str, str], actor: str) -> tuple[str, dict[str, object], dict[str, object]]:
        if self.engine == "jev":
            label, metadata = providers.jev_choose(state, choices, self.model)
        else:
            label, metadata = providers.openrouter_choose(state, choices, self.model)
        return label, metadata, {}

    def _v2_choice(self, state: dict[str, object], choices: dict[str, str], actor: str) -> tuple[str, dict[str, object], dict[str, object]]:
        proposal, metadata = providers.openrouter_choose(state, choices, self.model)
        if proposal == WAIT:
            return proposal, metadata, {}
        approved, probability, judge = providers.jev_score_proposal(state, choices, proposal, self.judge_model, threshold=self.threshold)
        judge_record: dict[str, object] = {"first": judge}
        selected = proposal
        if not approved:
            repaired, repaired_meta = providers.openrouter_choose(
                state, choices, self.model,
                feedback=f"the proposal scored {probability:.2f}, below the {self.threshold:.2f} threshold",
            )
            judge_record["repair_proposal"] = repaired
            judge_record["repair_turn"] = repaired_meta
            if repaired != WAIT:
                repair_approved, repair_probability, repair_judge = providers.jev_score_proposal(
                    state, choices, repaired, self.judge_model, threshold=self.threshold
                )
                judge_record["repair"] = repair_judge
                if repair_approved or repair_probability > probability:
                    selected = repaired
                    judge_record["accepted_repair"] = True
        metadata = {**metadata, "judge": judge_record}
        # The judge only scores proposals. `selected` always came from the LLM.
        return selected, metadata, {"judge": judge_record}

    def _run(self) -> None:
        participants = list(self.session.participants)
        index = 0
        while not self.stop_event.is_set() and not self.session.completed:
            if self.paused.is_set():
                time.sleep(0.08)
                continue
            actor = participants[index % len(participants)]
            index += 1
            state = self.session.snapshot(actor)
            choices = state.get("available_choices")
            if not isinstance(choices, dict) or not choices:
                time.sleep(0.05)
                continue
            started = time.monotonic()
            metadata: dict[str, object] = {}
            judge: dict[str, object] = {}
            try:
                if self.run.variant == "v2" and self.side == "right":
                    label, metadata, judge = self._v2_choice(state, choices, actor)
                else:
                    label, metadata, judge = self._provider_choice(state, choices, actor)
            except providers.ProviderError as error:
                # The UI remains useful before credentials are configured: the
                # session uses a legal local fallback and marks the trace.
                label, metadata = self._choice_offline(state, choices, actor)
                metadata["provider_error"] = str(error)
            except (OSError, ValueError) as error:
                label, metadata = self._choice_offline(state, choices, actor)
                metadata["provider_error"] = type(error).__name__
            candidate = next((item for item in state.get("available_candidates", []) if isinstance(item, dict) and item.get("label") == label), None)
            latency = int((time.monotonic() - started) * 1000)
            if not isinstance(candidate, dict):
                result = {"status": "rejected", "code": "provider_choice_not_offered"}
            else:
                action_type = str(candidate.get("action_type"))
                payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
                result = self.session.act(actor, int(state["session_seq"]), action_type, payload)
                if action_type.startswith("move"):
                    self._last_move_choice = label
            trace: dict[str, object] = {
                "actor": actor,
                "engine": "openrouter" if self.engine == "openrouter" else "jev",
                "model": self.model,
                "observed_session_seq": state.get("session_seq", 0),
                "activation_reason": "claim_review_requested" if state.get("completion", {}).get("claim_open") else "board_changed",
                "disposition": {MOVE_DISK: "act", POST_CLAIM: "claim", ASSESS_CLAIM: "assess", WAIT: "wait"}.get(str(candidate.get("action_type")) if isinstance(candidate, dict) else WAIT, "wait"),
                "selected": {"label": label, "action_type": candidate.get("action_type") if isinstance(candidate, dict) else WAIT},
                "status": result.get("status", "error"),
                "latency_ms": latency,
                "confidence": metadata.get("confidence"),
                "rationale": metadata.get("rationale"),
                "reason": metadata.get("reason"),
                "recent_event_count": len(self.events),
            }
            if "judge" in metadata:
                trace["judge"] = metadata["judge"]
            self._trace(trace)
            if self.session.completed:
                self.status = "complete"
                self.run.publish()
                return
            time.sleep(0.01)
        if self.status not in {"ended", "time_limit"}:
            self.status = "complete" if self.session.completed else "ended"
            self.run.publish()

    def pause(self) -> None:
        self.paused.set()
        self.status = "paused"
        self.run.publish()

    def resume(self) -> None:
        self.paused.clear()
        self.status = "live"
        self.run.publish()

    def end(self) -> None:
        self.stop_event.set()
        self.paused.clear()
        self.status = "ended"
        self.run.publish()


class ComparisonRun:
    def __init__(self, root: Path, bus: EventBus) -> None:
        self.root = root
        self.bus = bus
        self.lock = threading.RLock()
        self.variant = "v1"
        self.config: dict[str, object] | None = None
        self.lanes: dict[str, LaneWorker] = {}
        self.started_at_ms: int | None = None
        self.deadline_at_ms: int | None = None
        self.finished_at_ms: int | None = None
        self.generation = 0
        self.timer_thread: threading.Thread | None = None

    def _lane_public(self, lane: LaneWorker) -> dict[str, object]:
        return {
            "status": lane.status,
            "engine": lane.engine,
            "model": lane.model,
            "solver_count": len(lane.session.participants),
            "participants": list(lane.session.participants),
            "feed": {"projection": lane.session.snapshot(), "events": list(lane.events), "decisions": list(lane.decisions)},
            "projection": lane.session.snapshot(),
            "events": list(lane.events),
            "decisions": list(lane.decisions),
            "event_total": len(lane.full_events),
        }

    def public(self, status: str | None = None) -> dict[str, object]:
        with self.lock:
            now = int(time.time() * 1000)
            if self.finished_at_ms is not None:
                timer: dict[str, object] = {"status": "complete", "started_at_ms": self.started_at_ms, "deadline_at_ms": self.deadline_at_ms, "finished_at_ms": self.finished_at_ms, "elapsed_ms": max(0, self.finished_at_ms - (self.started_at_ms or self.finished_at_ms))}
            elif self.started_at_ms is None:
                timer = {"status": "idle" if self.config is None else "preparing"}
            else:
                timer = {"status": "running", "started_at_ms": self.started_at_ms, "deadline_at_ms": self.deadline_at_ms}
                if self.deadline_at_ms is not None and now >= self.deadline_at_ms:
                    timer["status"] = "time_limit"
            default_status = "idle" if self.config is None else ("complete" if self.finished_at_ms else "running")
            config = {key: value for key, value in (self.config or {}).items() if key not in {"api_key", "secret"}}
            return {"type": "comparison", "variant": self.variant, "status": status or default_status, "config": config, "timer": timer, "sides": {side: self._lane_public(lane) for side, lane in self.lanes.items()} or {"left": {"status": "idle"}, "right": {"status": "idle"}}}

    def publish(self, status: str | None = None) -> None:
        self.bus.publish(self.public(status))

    def start(self, config: dict[str, object]) -> dict[str, object]:
        with self.lock:
            if any(lane.thread.is_alive() for lane in self.lanes.values()):
                raise HanoiError("comparison_already_running")
            self.generation += 1
            generation = self.generation
            self.variant = config.get("variant", "v1") if config.get("variant", "v1") in {"v1", "v2"} else "v1"
            disks = _int(config.get("disks", 3), "disks", 1, 10)
            duration = _int(config.get("duration_seconds", DEFAULT_DURATION), "duration_seconds", 1, 3600)
            count = _int(config.get("solver_count", DEFAULT_SOLVERS), "solver_count", 1, MAX_SOLVERS)
            left_count = _int(config.get("left_solver_count", count), "left_solver_count", 1, MAX_SOLVERS)
            right_count = _int(config.get("right_solver_count", count), "right_solver_count", 1, MAX_SOLVERS)
            model = _model(config.get("model"), providers.DEFAULT_MODEL)
            judge_model = _model(config.get("judge_model"), providers.DEFAULT_JEV_MODEL)
            threshold = _float(config.get("judge_threshold", 0.5), "judge_threshold", 0.0, 1.0)
            randomize = config.get("randomize_board", False)
            if not isinstance(randomize, bool):
                raise HanoiError("randomize_board_invalid")
            seed_value = config.get("board_seed")
            seed = None if seed_value is None else _int(seed_value, "board_seed", 0, 9_007_199_254_740_991)
            if randomize and seed is None:
                seed = secrets.randbits(63)
            seed_for_names = seed or 1
            from .game import initial_board
            board = initial_board(disks, randomize=randomize, seed=seed)
            left_people = _participants(left_count, seed_for_names)
            right_people = _participants(right_count, seed_for_names + 17)
            self.config = {"variant": self.variant, "disks": disks, "duration_seconds": duration, "solver_count": count, "left_solver_count": left_count, "right_solver_count": right_count, "model": model, "judge_model": judge_model, "judge_threshold": threshold, "randomize_board": randomize, "board_seed": seed}
            self.started_at_ms = int(time.time() * 1000)
            self.deadline_at_ms = self.started_at_ms + duration * 1000
            self.finished_at_ms = None
            self.lanes = {
                "left": LaneWorker(self, "left", HanoiSession(disks, participants=left_people, board=copy.deepcopy(board)), "openrouter", model, judge_model, threshold),
                "right": LaneWorker(self, "right", HanoiSession(disks, participants=right_people, board=copy.deepcopy(board)), "jev" if self.variant == "v1" else "openrouter", model if self.variant == "v2" else judge_model, judge_model, threshold),
            }
            for lane in self.lanes.values():
                lane.session.on_event = lane._record
            self.publish("preparing")
            for lane in self.lanes.values():
                lane.start()
            self.timer_thread = threading.Thread(target=self._timer, args=(generation,), daemon=True, name="hanoi-timer")
            self.timer_thread.start()
            self.publish("running")
            return {"status": "running", "variant": self.variant, "board_seed": seed}

    def _timer(self, generation: int) -> None:
        while generation == self.generation:
            time.sleep(0.5)
            with self.lock:
                if generation != self.generation:
                    return
                if all(lane.session.completed for lane in self.lanes.values()):
                    self.finished_at_ms = int(time.time() * 1000)
                    self.publish("complete")
                    return
                if self.deadline_at_ms is not None and int(time.time() * 1000) >= self.deadline_at_ms:
                    for lane in self.lanes.values():
                        if lane.status not in {"complete", "ended"}:
                            lane.status = "time_limit"
                        lane.stop_event.set()
                    self.finished_at_ms = int(time.time() * 1000)
                    self.publish("time_limit")
                    return
                self.publish()

    def pause(self, side: str, value: bool) -> dict[str, object]:
        lane = self.lanes.get(side)
        if lane is None:
            raise HanoiError("side_invalid")
        if value:
            lane.pause()
        else:
            lane.resume()
        return {"status": lane.status, "side": side}

    def end(self, side: str) -> dict[str, object]:
        lane = self.lanes.get(side)
        if lane is None:
            raise HanoiError("side_invalid")
        lane.end()
        return {"status": "ended", "side": side}

    def stop(self) -> None:
        with self.lock:
            self.generation += 1
            for lane in self.lanes.values():
                lane.stop_event.set()
            self.lanes = {}
            self.config = None
            self.started_at_ms = None
            self.deadline_at_ms = None
            self.finished_at_ms = None
            self.publish("idle")


class ComparisonServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], root: Path) -> None:
        super().__init__(address, ComparisonHandler)
        self.root = root
        self.bus = EventBus()
        self.run = ComparisonRun(root, self.bus)


class ComparisonHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _headers(self, content_type: str, length: int | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
        if length is not None:
            self.send_header("Content-Length", str(length))

    def _send(self, status: HTTPStatus, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self._headers(content_type, len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        server: ComparisonServer = self.server  # type: ignore[assignment]
        path = urllib.parse.urlsplit(self.path).path
        if path in {"/", "/v2/", "/v2"}:
            # The page selects its lane wiring from the path, so both views
            # stay visually identical while remaining separate URLs.
            name = "index.html"
            try:
                body = (server.root / "hanoi" / "web" / name).read_bytes()
            except OSError:
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, b"demo unavailable\n", "text/plain")
                return
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
        elif path == "/config":
            body = json.dumps({"models": providers.model_catalog(), "defaults": {"solver_count": DEFAULT_SOLVERS, "duration_seconds": DEFAULT_DURATION, "port": DEFAULT_PORT}}, separators=(",", ":")).encode()
            self._send(HTTPStatus.OK, body)
        elif path == "/healthz":
            self._send(HTTPStatus.OK, b"ok\n", "text/plain")
        elif path == "/events":
            self._events(server)
        elif path == "/feed":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            side = (query.get("side") or [""])[0]
            lane = server.run.lanes.get(side)
            if lane is None:
                self._send(HTTPStatus.BAD_REQUEST, b'{"status":"error","code":"side_invalid"}\n')
                return
            body = json.dumps({"side": side, "count": len(lane.full_events), "events": list(lane.full_events)}, separators=(",", ":")).encode()
            self.send_response(HTTPStatus.OK)
            self._headers("application/json", len(body))
            self.send_header("Content-Disposition", f'inline; filename="hanoi-{side}-feed.json"')
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")

    def do_POST(self) -> None:
        server: ComparisonServer = self.server  # type: ignore[assignment]
        if self.path not in {"/start", "/stop", "/pause", "/resume", "/end"}:
            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
            return
        try:
            if self.path == "/stop":
                server.run.stop()
                self._send(HTTPStatus.OK, b'{"status":"idle"}\n')
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY:
                raise HanoiError("body_oversize")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise HanoiError("body_invalid")
            if self.path == "/start":
                result = server.run.start(body)
            else:
                side = body.get("side")
                if side not in {"left", "right"}:
                    raise HanoiError("side_invalid")
                result = server.run.pause(side, self.path == "/pause") if self.path in {"/pause", "/resume"} else server.run.end(side)
        except (HanoiError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self._send(HTTPStatus.BAD_REQUEST, json.dumps({"status": "error", "code": str(error)}).encode())
            return
        self._send(HTTPStatus.ACCEPTED, json.dumps(result, separators=(",", ":")).encode())

    def _events(self, server: ComparisonServer) -> None:
        subscriber = server.bus.subscribe(self.headers.get("Last-Event-ID"))
        if subscriber is None:
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, b"viewer capacity reached\n", "text/plain")
            return
        try:
            self.send_response(HTTPStatus.OK)
            self._headers("text/event-stream; charset=utf-8")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.flush()
            while True:
                try:
                    cursor, payload = subscriber.get(timeout=10)
                    self.wfile.write(f"id: {cursor}\nevent: comparison\ndata: {payload}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            server.bus.unsubscribe(subscriber)

    def log_message(self, _format: str, *_arguments: object) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        raise SystemExit("port must be between 1024 and 65535")
    root = args.root.resolve()
    providers.load_dotenv(root)
    server = ComparisonServer(("127.0.0.1", args.port), root)
    print(json.dumps({"status": "ready", "url": f"http://127.0.0.1:{args.port}/"}), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        server.run.stop()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
