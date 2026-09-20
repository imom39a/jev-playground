import json
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from hanoi.game import HanoiError, HanoiSession
from hanoi import providers
from hanoi.server import ComparisonHandler, ComparisonRun, EventBus, ComparisonServer, LaneWorker


class ServerTests(unittest.TestCase):
    def test_public_state_does_not_include_credentials(self):
        run = ComparisonRun(Path("."), EventBus())
        run.config = {"model": "safe", "api_key": "do-not-copy"}
        public = run.public()
        self.assertNotIn("api_key", json.dumps(public))

    def test_http_health_and_pages(self):
        server = ComparisonServer(("127.0.0.1", 0), Path(__file__).resolve().parents[2])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port
            for path in ("/", "/v2/", "/config", "/healthz"):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertNotIn("branded transport", response.read().decode("utf-8", "replace").lower())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_page_uses_neutral_session_sequence_and_lane_controls(self):
        page = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("room_seq", page)
        self.assertNotIn("observed_room_seq", page)
        self.assertIn("session_seq", page)
        self.assertIn("lane-pause", page)
        self.assertIn("lane-end", page)
        self.assertIn("payload.variant!==variant", page)
        self.assertNotIn("leftCount", page)
        self.assertNotIn("rightCount", page)
        self.assertIn("d.probabilities", page)
        self.assertIn("JEV veto", page)

    def test_comparison_always_has_one_participant_per_lane(self):
        run = ComparisonRun(Path("."), EventBus())
        with self.assertRaises(HanoiError):
            run.start({"solver_count": 2})
        with patch("hanoi.server.LaneWorker.start"):
            run.start({"duration_seconds": 1})
        try:
            self.assertEqual(run.config["solver_count"], 1)
            self.assertEqual(run.config["left_solver_count"], 1)
            self.assertEqual(run.config["right_solver_count"], 1)
            self.assertEqual([len(lane.session.participants) for lane in run.lanes.values()], [1, 1])
            self.assertEqual(run.public()["sides"]["left"]["solver_count"], 1)
            self.assertEqual(run.public()["sides"]["right"]["solver_count"], 1)
        finally:
            run.stop()

    def test_worker_adds_bounded_own_decisions_to_provider_state(self):
        run = ComparisonRun(Path("."), EventBus())
        worker = LaneWorker(run, "left", HanoiSession(3, participants=["llm-solver"]), "openrouter", "model", "judge", 0.5)
        for index in range(6):
            worker.decisions.append({
                "actor": "llm-solver",
                "observed_session_seq": index,
                "selected": {"label": f"move-{index}"},
                "status": "accepted",
                "reason": "progress",
                "disposition": "act",
            })
        state = worker._decision_state("llm-solver")
        self.assertEqual(len(state["recent_own_decisions"]), 4)
        self.assertEqual(state["recent_own_decisions"][0]["label"], "move-2")
        self.assertEqual(state["recent_own_decisions"][0]["disposition"], "act")
        self.assertEqual(state["activation_reason"], "board_changed")
        self.assertEqual(state["schema"], "jev-playground.hanoi.solver-state.v1")
        self.assertEqual(state["board"], {"A": [3, 2, 1], "B": [], "C": []})
        self.assertIsInstance(state["cycle_hint"], bool)
        self.assertNotIn("recent_history", state)

    def _v2_worker(self) -> tuple[LaneWorker, dict[str, object], dict[str, str]]:
        run = ComparisonRun(Path("."), EventBus())
        worker = LaneWorker(run, "right", HanoiSession(3, participants=["solver"]), "openrouter", "model", "judge", 0.5)
        state = worker.session.snapshot("solver")
        return worker, state, state["available_choices"]

    def test_v2_approved_original_needs_no_repair(self):
        worker, state, choices = self._v2_worker()
        with patch.object(providers, "openrouter_choose", return_value=("move:A>B:1", {"rationale": "first"})) as choose, patch.object(
            providers, "jev_score_proposal", return_value=(True, 0.8, {"approved": True, "probability": 0.8})
        ) as score:
            label, metadata, _ = worker._v2_choice(state, choices, "solver")
        self.assertEqual(label, "move:A>B:1")
        self.assertEqual(choose.call_count, 1)
        self.assertEqual(score.call_count, 1)
        self.assertNotIn("accepted_repair", metadata["judge"])

    def test_v2_executes_only_an_approved_different_repair(self):
        worker, state, choices = self._v2_worker()
        with patch.object(providers, "openrouter_choose", side_effect=[
            ("move:A>B:1", {"rationale": "first"}),
            ("move:A>C:1", {"rationale": "repair"}),
        ]) as choose, patch.object(providers, "jev_score_proposal", side_effect=[
            (False, 0.2, {"approved": False, "probability": 0.2}),
            (True, 0.7, {"approved": True, "probability": 0.7}),
        ]):
            label, metadata, _ = worker._v2_choice(state, choices, "solver")
        self.assertEqual(label, "move:A>C:1")
        self.assertEqual(metadata["rationale"], "repair")
        self.assertTrue(metadata["judge"]["accepted_repair"])
        self.assertIn("Choose a different offered label", choose.call_args_list[1].kwargs["feedback"])
        self.assertEqual(choose.call_args_list[1].args[1], choices)

    def test_v2_below_threshold_repair_is_vetoed_even_if_score_improves(self):
        worker, state, choices = self._v2_worker()
        with patch.object(providers, "openrouter_choose", side_effect=[
            ("move:A>B:1", {"rationale": "first"}),
            ("move:A>C:1", {"rationale": "repair"}),
        ]), patch.object(providers, "jev_score_proposal", side_effect=[
            (False, 0.2, {"approved": False, "probability": 0.2}),
            (False, 0.49, {"approved": False, "probability": 0.49}),
        ]):
            label, metadata, _ = worker._v2_choice(state, choices, "solver")
        self.assertIsNone(label)
        self.assertEqual(metadata["judge"]["outcome"], "vetoed")
        self.assertNotIn("accepted_repair", metadata["judge"])

    def test_v2_duplicate_repair_is_vetoed_without_rescoring(self):
        worker, state, choices = self._v2_worker()
        with patch.object(providers, "openrouter_choose", side_effect=[
            ("move:A>B:1", {}), ("move:A>B:1", {})
        ]), patch.object(
            providers, "jev_score_proposal", return_value=(False, 0.2, {"approved": False, "probability": 0.2})
        ) as score:
            label, metadata, _ = worker._v2_choice(state, choices, "solver")
        self.assertIsNone(label)
        self.assertEqual(score.call_count, 1)
        self.assertEqual(metadata["judge"]["repair_invalid"], "same_proposal")

    def test_provider_state_uses_action_events_not_ui_decision_traces(self):
        run = ComparisonRun(Path("."), EventBus())
        worker = LaneWorker(run, "right", HanoiSession(3, participants=["solver"]), "openrouter", "model", "judge", 0.5)
        worker.session.act("solver", 0, "move_disk", {"from": "A", "to": "C", "disk": 1})
        worker.events.append({"kind": "participant_decision", "actor": "solver"})
        state = worker._decision_state("solver")
        self.assertEqual([event["kind"] for event in state["recent_events"]], ["disk_moved"])
        self.assertEqual(state["session_seq"], 1)
        self.assertEqual(state["board"], {"A": [3, 2], "B": [], "C": [1]})

    def test_v2_veto_records_no_action_and_does_not_advance_session(self):
        worker, _, _ = self._v2_worker()
        worker.run.variant = "v2"
        captured: list[dict[str, object]] = []

        def capture(trace: dict[str, object]) -> None:
            captured.append(trace)
            worker.stop_event.set()

        with patch.object(worker, "_v2_choice", return_value=(
            None,
            {"judge": {"outcome": "vetoed"}, "reason": "judge_rejected"},
            {"judge": {"outcome": "vetoed"}},
        )), patch.object(worker, "_trace", side_effect=capture):
            worker._run()
        self.assertEqual(worker.session.session_seq, 0)
        self.assertEqual(captured[0]["status"], "vetoed")
        self.assertEqual(captured[0]["disposition"], "defer")
        self.assertIsNone(captured[0]["selected"])

    def test_provider_failure_submits_no_offline_action(self):
        worker, _, _ = self._v2_worker()
        worker.run.variant = "v1"
        captured: list[dict[str, object]] = []

        def capture(trace: dict[str, object]) -> None:
            captured.append(trace)

        with patch.object(worker, "_provider_choice", side_effect=providers.ProviderError("provider_request_failed")), patch.object(
            worker, "_trace", side_effect=capture
        ):
            worker._run()
        self.assertEqual(worker.session.session_seq, 0)
        self.assertEqual(worker.status, "error")
        self.assertEqual(captured[0]["status"], "error")
        self.assertIsNone(captured[0]["selected"])

    def test_provider_timeout_uses_remaining_run_budget(self):
        worker, _, _ = self._v2_worker()
        worker.run.deadline_at_ms = int(time.time() * 1000) - 1
        with self.assertRaisesRegex(providers.ProviderError, "run_deadline_reached"):
            worker._provider_timeout(30.0)


if __name__ == "__main__":
    unittest.main()
