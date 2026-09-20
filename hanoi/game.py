"""The bounded, provider-independent Tower of Hanoi state machine.

The browser only sees projections made by :class:`HanoiSession`.  This module
owns move legality, revision fencing, claim quorum, and completion.  A solved
board is evidence for a participant; it is never a completion transition by
itself.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import json
import random
from typing import Any, Callable

RODS = ("A", "B", "C")
MAX_DISKS = 10
MAX_MOVES = 10_000
MAX_HISTORY = 64
MOVE_DISK = "move_disk"
POST_CLAIM = "post_completion_claim"
ASSESS_CLAIM = "assess_claim"
WAIT = "wait"
ASSESSMENTS = ("endorse", "challenge", "defer")


class HanoiError(ValueError):
    """A closed, safe error code suitable for an HTTP response."""


@dataclass(frozen=True)
class Move:
    source: str
    destination: str
    disk: int

    def as_dict(self) -> dict[str, object]:
        return {"from": self.source, "to": self.destination, "disk": self.disk}

    def label(self) -> str:
        return f"move:{self.source}>{self.destination}:{self.disk}"


def _validate_board(board: object, disks: int) -> dict[str, list[int]]:
    if not isinstance(board, dict) or set(board) != set(RODS):
        raise HanoiError("board_invalid")
    result: dict[str, list[int]] = {}
    seen: list[int] = []
    for rod in RODS:
        stack = board[rod]
        if not isinstance(stack, list) or any(
            isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= disks
            for item in stack
        ):
            raise HanoiError("board_invalid")
        if any(stack[index] <= stack[index + 1] for index in range(len(stack) - 1)):
            raise HanoiError("board_invalid")
        result[rod] = list(stack)
        seen.extend(stack)
    if sorted(seen) != list(range(1, disks + 1)):
        raise HanoiError("board_invalid")
    return result


def initial_board(disks: int, *, randomize: bool = False, seed: int | None = None) -> dict[str, list[int]]:
    """Return the default tower or one deterministic reachable mid-state."""
    if isinstance(disks, bool) or not isinstance(disks, int) or not 1 <= disks <= MAX_DISKS:
        raise HanoiError("disks_invalid")
    board = {"A": list(range(disks, 0, -1)), "B": [], "C": []}
    if not randomize:
        return board
    rng = random.Random(seed)
    previous: Move | None = None
    # A short random walk makes a state that remains easy to inspect and is
    # always solvable because every position is reached by legal moves.
    steps = max(2, min(24, disks * 3 + 2))
    for _ in range(steps):
        choices = _legal_moves(board, disks)
        if not choices:
            break
        if previous is not None:
            reverse = (previous.destination, previous.source, previous.disk)
            filtered = [m for m in choices if (m.source, m.destination, m.disk) != reverse]
            if filtered:
                choices = filtered
        rng.shuffle(choices)
        move = choices[0]
        source = board[move.source]
        destination = board[move.destination]
        source.pop()
        destination.append(move.disk)
        previous = move
    # A random starting board should be a genuine mid-state. Recompute legal
    # moves after the walk so a just-reached target cannot reuse stale choices.
    trivial = board["A"] == list(range(disks, 0, -1)) or board["C"] == list(range(disks, 0, -1))
    if trivial:
        choices = _legal_moves(board, disks)
        nontrivial: list[Move] = []
        source_board = {"A": list(range(disks, 0, -1)), "B": [], "C": []}
        target_board = {"A": [], "B": [], "C": list(range(disks, 0, -1))}
        for candidate in choices:
            source = board[candidate.source]
            destination = board[candidate.destination]
            source.pop()
            destination.append(candidate.disk)
            if board != source_board and board != target_board:
                nontrivial.append(candidate)
            destination.pop()
            source.append(candidate.disk)
        if nontrivial:
            choices = nontrivial
        if choices:
            rng.shuffle(choices)
            move = choices[0]
            board[move.source].pop()
            board[move.destination].append(move.disk)
    return board


def _legal_moves(board: dict[str, list[int]], disks: int) -> list[Move]:
    result: list[Move] = []
    for source in RODS:
        if not board[source]:
            continue
        disk = board[source][-1]
        for destination in RODS:
            if source == destination:
                continue
            top = board[destination][-1] if board[destination] else None
            if top is None or top > disk:
                result.append(Move(source, destination, disk))
    return result


def board_fingerprint(board: dict[str, list[int]]) -> str:
    encoded = json.dumps(board, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.blake2s(encoded, digest_size=8).hexdigest()


def _safe_actor(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 64 or any(ch.isspace() for ch in value):
        raise HanoiError("actor_invalid")
    return value


class HanoiSession:
    """One authoritative lane with a local sequence and claim lifecycle."""

    def __init__(
        self,
        disks: int = 3,
        *,
        participants: list[str] | None = None,
        randomize: bool = False,
        seed: int | None = None,
        move_limit: int = MAX_MOVES,
        board: dict[str, list[int]] | None = None,
        on_event: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        if isinstance(move_limit, bool) or not isinstance(move_limit, int) or not 1 <= move_limit <= MAX_MOVES:
            raise HanoiError("move_limit_invalid")
        if participants is None:
            participants = ["solver-1"]
        if not 1 <= len(participants) <= 16 or len(set(participants)) != len(participants):
            raise HanoiError("participants_invalid")
        self.disks = disks
        self.board = _validate_board(board, disks) if board is not None else initial_board(disks, randomize=randomize, seed=seed)
        self.participants = [_safe_actor(item) for item in participants]
        self.move_limit = move_limit
        self.on_event = on_event
        self.session_seq = 0
        self.round = 1
        self.work_revision = 0
        self.phase = "solving"
        self.moves = 0
        self.contributions = {name: 0 for name in self.participants}
        self.claim: dict[str, object] | None = None
        self.assessments: dict[str, str] = {}
        self.last_move: dict[str, object] | None = None
        self.history: deque[dict[str, object]] = deque(maxlen=MAX_HISTORY)
        self.history.append({"revision": 0, "fingerprint": board_fingerprint(self.board), "move": None})
        self.events: deque[dict[str, object]] = deque(maxlen=128)

    @property
    def target_reached(self) -> bool:
        return self.board == {"A": [], "B": [], "C": list(range(self.disks, 0, -1))}

    @property
    def completed(self) -> bool:
        return self.phase == "complete"

    @property
    def quorum(self) -> int:
        return len(self.participants) // 2 + 1

    def legal_moves(self) -> list[Move]:
        if self.completed or self.moves >= self.move_limit:
            return []
        return _legal_moves(self.board, self.disks)

    def _cycle_hint(self) -> dict[str, object]:
        fingerprints = [str(item["fingerprint"]) for item in self.history]
        current = fingerprints[-1]
        repeated = sum(item == current for item in fingerprints[:-1])
        reverse = None
        if self.last_move:
            move = self.last_move.get("move")
            if isinstance(move, dict):
                reverse = {"from": move.get("to"), "to": move.get("from"), "disk": move.get("disk")}
        return {
            "current_revisits": repeated,
            "recent_states": len(fingerprints),
            "avoid_reverse": reverse,
            "hint": "break_cycle" if repeated else "progress",
        }

    def _recent_moves(self) -> list[dict[str, object]]:
        moves: list[dict[str, object]] = []
        for item in list(self.history)[-16:]:
            move = item.get("move")
            if not isinstance(move, dict):
                continue
            source, destination, disk = move.get("from"), move.get("to"), move.get("disk")
            if source in RODS and destination in RODS and isinstance(disk, int):
                moves.append({
                    "revision": item.get("revision"),
                    "label": f"move:{source}>{destination}:{disk}",
                    "move": dict(move),
                })
        return moves[-8:]

    def _choice_history(self, moves: list[Move]) -> dict[str, dict[str, object]]:
        """Describe bounded history facts for each legal successor.

        These facts do not choose a move. They make the consequences of each
        offered move explicit so a stateless provider need not reconstruct the
        session's bounded memory from scratch on every turn.
        """
        visits = Counter(str(item.get("fingerprint")) for item in self.history)
        recent_labels = {str(item["label"]) for item in self._recent_moves()}
        undo_label: str | None = None
        if self.last_move and isinstance(self.last_move.get("move"), dict):
            last = self.last_move["move"]
            undo_label = f"move:{last.get('to')}>{last.get('from')}:{last.get('disk')}"
        result: dict[str, dict[str, object]] = {}
        for move in moves:
            resulting = {rod: list(self.board[rod]) for rod in RODS}
            resulting[move.source].pop()
            resulting[move.destination].append(move.disk)
            label = move.label()
            is_target = resulting == {"A": [], "B": [], "C": list(range(self.disks, 0, -1))}
            result[label] = {
                "visits": 0 if is_target else visits[board_fingerprint(resulting)],
                "repeats_recent": label in recent_labels,
                "undoes_last": label == undo_label,
            }
        return result

    def _completion(self) -> dict[str, object]:
        claim = self.claim
        active = claim is not None and not self.completed
        electorate = list(claim["electorate"]) if claim else list(self.participants)
        endorsements = sum(value == "endorse" for value in self.assessments.values())
        approval = (1 if claim and claim.get("claimant") in electorate else 0) + endorsements
        result: dict[str, object] = {
            "claim_open": active,
            "assessments_by_member": dict(self.assessments),
            "endorsement_count": endorsements,
            "approval_count": approval,
            "quorum": int(claim["quorum"]) if claim else self.quorum,
        }
        if claim is not None:
            result["claim"] = {
                "claimant_member_id": claim["claimant"],
                "claim_round": claim["claim_round"],
                "work_revision": claim["work_revision"],
                "electorate_size": len(electorate),
                "quorum": claim["quorum"],
            }
        return result

    def candidates(self, actor: str | None = None) -> list[dict[str, object]]:
        """Return a closed candidate set shared by provider and UI."""
        values: list[dict[str, object]] = []
        moves = self.legal_moves()
        history = self._choice_history(moves)
        for move in moves:
            facts = history[move.label()]
            notes: list[str] = []
            visits = facts["visits"]
            if isinstance(visits, int) and visits > 0:
                notes.append(f"resulting board was seen {visits} time(s) before")
            if facts["repeats_recent"]:
                notes.append(
                    "same directed move appeared recently"
                    + (", but this resulting board is unseen" if visits == 0 else "")
                )
            if facts["undoes_last"]:
                notes.append("reverses the most recent move")
            description = f"Move disk {move.disk} from {move.source} to {move.destination}."
            if notes:
                description = f"{description} ({'; '.join(notes)})."
            if self.target_reached:
                description = f"{description} This moves a disk off the completed target tower."
            values.append({
                "label": move.label(),
                "action_type": MOVE_DISK,
                "payload": move.as_dict(),
                "description": description,
            })
        if not self.completed:
            if self.claim is None:
                claim_description = "Claim completion only if you judge that the objective is complete."
                if self.target_reached:
                    claim_description = "The complete tower is on target rod C; record your completion claim."
                values.append({
                    "label": POST_CLAIM,
                    "action_type": POST_CLAIM,
                    "payload": {"work_revision": self.work_revision},
                    "description": claim_description,
                })
            elif actor is not None and actor != self.claim.get("claimant") and actor in self.claim.get("electorate", []):
                for assessment in ASSESSMENTS:
                    values.append({
                        "label": f"assess:{assessment}",
                        "action_type": ASSESS_CLAIM,
                        "payload": {
                            "work_revision": self.work_revision,
                            "claim_round": self.claim["claim_round"],
                            "assessment": assessment,
                        },
                        "description": f"Review the open claim and {assessment} it.",
                    })
            # This standalone runner has no external scheduler that can wake a
            # waiting participant. Offer wait only if no concrete action exists.
            if not values:
                values.append({
                    "label": WAIT,
                    "action_type": WAIT,
                    "payload": {},
                    "description": "Wait for a new observation or request.",
                })
        return values

    def available_choices(self, actor: str | None = None) -> dict[str, str]:
        return {str(item["label"]): str(item["description"]) for item in self.candidates(actor)}

    def snapshot(self, actor: str | None = None) -> dict[str, object]:
        candidates = self.candidates(actor)
        choice_history = self._choice_history(self.legal_moves())
        recent_moves = self._recent_moves()
        immediate_undo = None
        if self.last_move and isinstance(self.last_move.get("move"), dict):
            last = self.last_move["move"]
            immediate_undo = f"move:{last.get('to')}>{last.get('from')}:{last.get('disk')}"
        current_visits = sum(
            item.get("fingerprint") == board_fingerprint(self.board) for item in self.history
        )
        return {
            "session_seq": self.session_seq,
            "disks": self.disks,
            "board": {rod: list(self.board[rod]) for rod in RODS},
            "phase": self.phase,
            "round": self.round,
            "work_revision": self.work_revision,
            "move_limit": self.move_limit,
            "objective": {"source_rod": "A", "target_rod": "C", "description": "Move the full tower from A to C."},
            "rules": {"largest_disk_on_bottom": True, "one_disk_per_action": True},
            "outcome": {"moves": self.moves, "status": "participant_accepted_completion" if self.completed else "in_progress"},
            "completion": self._completion(),
            "target_reached": self.target_reached,
            "last_move": dict(self.last_move) if self.last_move else None,
            "recent_moves": recent_moves,
            "immediate_undo": immediate_undo,
            "contributions_by_member": dict(self.contributions),
            "recent_history": list(self.history)[-16:],
            "cycle_hint": self._cycle_hint(),
            "state_visit": {"visits": current_visits, "cycle_hint": current_visits > 1},
            "choice_history": choice_history,
            "available_candidates": candidates,
            "available_choices": {str(item["label"]): str(item["description"]) for item in candidates},
        }

    def _emit(self, event: dict[str, object]) -> None:
        # Keep only a closed public vocabulary and bounded scalar values.
        safe = {
            "kind": str(event.get("kind", "event"))[:64],
            "actor": str(event.get("actor", "participant"))[:64],
            "session_seq": self.session_seq,
            "round": self.round,
            "work_revision": self.work_revision,
        }
        for key in ("status", "action_type", "assessment", "claim_open", "approval_count", "quorum", "claim_round"):
            if key in event:
                value = event[key]
                if isinstance(value, (str, int, bool)) and not isinstance(value, float):
                    safe[key] = value
        move = event.get("move")
        if isinstance(move, dict):
            try:
                validated = Move(str(move["from"]), str(move["to"]), int(move["disk"]))
                if validated.source in RODS and validated.destination in RODS and validated.source != validated.destination and 1 <= validated.disk <= self.disks:
                    safe["move"] = validated.as_dict()
            except (KeyError, TypeError, ValueError):
                pass
        self.events.append(safe)
        if self.on_event:
            self.on_event(dict(safe))

    def _reject(self, actor: str, action_type: str, code: str, *, stale: bool = False) -> dict[str, object]:
        result = {"status": "stale" if stale else "rejected", "code": code, "action_type": action_type, "current_session_seq": self.session_seq}
        self._emit({"kind": "action_rejected", "actor": actor, "action_type": action_type, "status": result["status"]})
        return result

    def act(self, actor: str, expected_session_seq: int, action_type: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        actor = _safe_actor(actor)
        if isinstance(expected_session_seq, bool) or not isinstance(expected_session_seq, int) or expected_session_seq < 0:
            raise HanoiError("session_seq_invalid")
        if expected_session_seq != self.session_seq:
            return self._reject(actor, action_type, "stale_head", stale=True)
        if self.completed:
            return self._reject(actor, action_type, "activity_complete")
        payload = payload if isinstance(payload, dict) else {}
        candidate = next((item for item in self.candidates(actor) if item["action_type"] == action_type and item["payload"] == payload), None)
        if action_type not in {MOVE_DISK, POST_CLAIM, ASSESS_CLAIM, WAIT}:
            return self._reject(actor, action_type, "action_type_invalid")
        if action_type == WAIT and candidate is None:
            return self._reject(actor, action_type, "action_not_offered")
        self.session_seq += 1
        self.round += 1
        if action_type == WAIT:
            self._emit({"kind": "participant_waited", "actor": actor, "action_type": WAIT, "status": "accepted"})
            return {"status": "accepted", "action_type": WAIT, "session_seq": self.session_seq, "work_revision": self.work_revision, "phase": self.phase}
        if action_type == MOVE_DISK:
            if set(payload) != {"from", "to", "disk"} or payload.get("from") not in RODS or payload.get("to") not in RODS or payload.get("from") == payload.get("to") or isinstance(payload.get("disk"), bool) or not isinstance(payload.get("disk"), int):
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "invalid_move")
            move = Move(str(payload["from"]), str(payload["to"]), int(payload["disk"]))
            if not 1 <= move.disk <= self.disks:
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "invalid_move")
            source = self.board[move.source]
            destination = self.board[move.destination]
            if not source:
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "invalid_move")
            if source[-1] != move.disk:
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "wrong_disk")
            if destination and destination[-1] < move.disk:
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "blocked_disk")
            source.pop(); destination.append(move.disk)
            self.moves += 1
            self.work_revision += 1
            self.contributions[actor] = self.contributions.get(actor, 0) + 1
            self.last_move = {"member_id": actor, "move": move.as_dict(), "round": self.round}
            self.claim = None
            self.assessments = {}
            self.history.append({"revision": self.work_revision, "fingerprint": board_fingerprint(self.board), "move": move.as_dict()})
            self._emit({"kind": "disk_moved", "actor": actor, "action_type": MOVE_DISK, "status": "accepted", "move": move.as_dict(), "claim_open": False})
        elif action_type == POST_CLAIM:
            if set(payload) != {"work_revision"} or isinstance(payload.get("work_revision"), bool) or not isinstance(payload.get("work_revision"), int):
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "claim_revision_invalid")
            if payload.get("work_revision") != self.work_revision:
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "claim_superseded")
            if self.claim is not None:
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "claim_open")
            self.claim = {"claimant": actor, "work_revision": self.work_revision, "claim_round": self.round, "electorate": list(self.participants), "quorum": self.quorum}
            self.assessments = {}
            if self.quorum <= 1:
                self.phase = "complete"
            completion = self._completion()
            self._emit({"kind": "completion_claim_posted", "actor": actor, "action_type": POST_CLAIM, "status": "accepted", "claim_open": not self.completed, "approval_count": completion["approval_count"], "quorum": completion["quorum"], "claim_round": self.round})
        elif action_type == ASSESS_CLAIM:
            claim = self.claim
            if claim is None:
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "claim_absent")
            if set(payload) != {"work_revision", "claim_round", "assessment"} or isinstance(payload.get("work_revision"), bool) or not isinstance(payload.get("work_revision"), int) or isinstance(payload.get("claim_round"), bool) or not isinstance(payload.get("claim_round"), int):
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "assessment_invalid")
            if payload.get("work_revision") != self.work_revision or payload.get("claim_round") != claim["claim_round"]:
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "claim_superseded")
            if actor == claim["claimant"]:
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "claimant_cannot_assess")
            assessment = payload.get("assessment")
            if assessment not in ASSESSMENTS:
                self.session_seq -= 1; self.round -= 1
                return self._reject(actor, action_type, "assessment_invalid")
            self.assessments[actor] = str(assessment)
            completion = self._completion()
            if int(completion["approval_count"]) >= int(completion["quorum"]):
                self.phase = "complete"
            self._emit({"kind": "completion_claim_assessed", "actor": actor, "action_type": ASSESS_CLAIM, "status": "accepted", "assessment": assessment, "claim_open": not self.completed, "approval_count": completion["approval_count"], "quorum": completion["quorum"], "claim_round": claim["claim_round"]})
        else:
            self.session_seq -= 1; self.round -= 1
            return self._reject(actor, action_type, "action_type_invalid")
        return {"status": "accepted", "action_type": action_type, "session_seq": self.session_seq, "work_revision": self.work_revision, "phase": self.phase, "outcome": self.snapshot(actor)["outcome"]}


def sanitize_event(value: object) -> dict[str, object] | None:
    """Copy only bounded event metadata for the browser."""
    if not isinstance(value, dict):
        return None
    kind, actor = value.get("kind"), value.get("actor")
    seq = value.get("session_seq")
    if not isinstance(kind, str) or not kind or len(kind) > 64 or not isinstance(actor, str) or not actor or len(actor) > 64:
        return None
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        return None
    result: dict[str, object] = {"kind": kind, "actor": actor, "session_seq": seq}
    for key in ("round", "work_revision", "approval_count", "quorum", "claim_round"):
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= MAX_MOVES * 3:
            result[key] = item
    for key in ("status", "action_type", "assessment"):
        item = value.get(key)
        if isinstance(item, str) and item and len(item) <= 64 and not any(ch.isspace() for ch in item):
            result[key] = item
    if isinstance(value.get("claim_open"), bool):
        result["claim_open"] = value["claim_open"]
    move = value.get("move")
    if isinstance(move, dict) and set(move) == {"from", "to", "disk"}:
        source, dest, disk = move["from"], move["to"], move["disk"]
        if source in RODS and dest in RODS and source != dest and isinstance(disk, int) and not isinstance(disk, bool) and 1 <= disk <= MAX_DISKS:
            result["move"] = {"from": source, "to": dest, "disk": disk}
    return result
