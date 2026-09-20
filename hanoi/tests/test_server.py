import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from hanoi.server import ComparisonHandler, ComparisonRun, EventBus, ComparisonServer


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


if __name__ == "__main__":
    unittest.main()
