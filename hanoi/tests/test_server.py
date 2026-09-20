import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from hanoi.game import HanoiError, HanoiSession
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
            })
        state = worker._decision_state("llm-solver")
        self.assertEqual(len(state["recent_own_decisions"]), 4)
        self.assertEqual(state["recent_own_decisions"][0]["observed_session_seq"], 2)
        self.assertEqual(state["activation_reason"], "board_changed")


if __name__ == "__main__":
    unittest.main()
