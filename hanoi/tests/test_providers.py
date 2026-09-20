import json
import os
import unittest
from unittest.mock import patch

from hanoi import providers


class ProviderTests(unittest.TestCase):
    def test_openrouter_uses_closed_choice_and_no_browser_metadata(self):
        response = {"model": "chosen", "choices": [{"message": {"content": json.dumps({"label": "move:A>B:1", "confidence": 0.8, "rationale": "safe"})}}]}
        with patch.object(providers, "_http_json", return_value=response) as request:
            label, metadata = providers.openrouter_choose({"board": {}}, {"move:A>B:1": "move"}, "model", key="secret")
        self.assertEqual(label, "move:A>B:1")
        self.assertEqual(metadata["confidence"], 0.8)
        headers = request.call_args.args[2]
        self.assertEqual(headers, {"Authorization": "Bearer secret"})
        payload = request.call_args.args[1]
        self.assertEqual(payload["provider"], {"sort": "latency"})
        prompt = payload["messages"][1]["content"]
        self.assertIn("choice_history", prompt)
        self.assertIn("your previous decisions", prompt)
        self.assertIn("authoritative board at the current session sequence", prompt)
        self.assertIn("based_on_session_seq", prompt)

    def test_openrouter_accepts_canonical_action_payload(self):
        decision = {
            "based_on_session_seq": 3,
            "action": "move_disk",
            "payload": {"from": "A", "to": "B", "disk": 1},
        }
        response = {"choices": [{"message": {"content": json.dumps(decision)}}]}
        state = {
            "session_seq": 3,
            "available_candidates": [{
                "label": "move:A>B:1",
                "action_type": "move_disk",
                "payload": {"from": "A", "to": "B", "disk": 1},
            }],
        }
        with patch.object(providers, "_http_json", return_value=response):
            label, _ = providers.openrouter_choose(state, {"move:A>B:1": "move"}, "model", key="secret")
        self.assertEqual(label, "move:A>B:1")

    def test_jev_judge_only_scores_proposal(self):
        response = {"model": "jev", "answers": {"judge": {"noul": 0.72}}}
        with patch.object(providers, "_http_json", return_value=response) as request:
            approved, probability, metadata = providers.jev_score_proposal({}, {"move:A>B:1": "move"}, "move:A>B:1", key="secret", threshold=0.7)
        self.assertTrue(approved)
        self.assertEqual(probability, 0.72)
        self.assertEqual(metadata["proposed"], "move:A>B:1")
        payload = request.call_args.args[1]
        self.assertEqual(payload["questions"]["judge"]["type"], "noul")
        self.assertEqual(payload["state"]["proposed_action"], "move:A>B:1")
        instructions = payload["questions"]["judge"]["instructions"]
        self.assertIn("Does it best advance", instructions)
        self.assertIn("reverses the most recent move", instructions)
        self.assertIn("returns to a board already seen", instructions)

    def test_jev_choice_receives_completion_and_cycle_guidance(self):
        response = {"answers": {"choice": {"choice": "move:A>B:1", "confidence": 0.7, "probabilities": {"move:A>B:1": 0.7, "move:A>C:1": 0.3}}}}
        with patch.object(providers, "_http_json", return_value=response) as request:
            _, metadata = providers.jev_choose({}, {"move:A>B:1": "move"}, key="secret")
        instructions = request.call_args.args[1]["questions"]["choice"]["instructions"]
        self.assertIn("choice_history", instructions)
        self.assertIn("completion evidence", instructions)
        self.assertIn("deliberately breaking a cycle", instructions)
        self.assertEqual(metadata["probabilities"]["move:A>B:1"], 0.7)

    def test_dotenv_aliases(self):
        with patch.dict(os.environ, {}, clear=True):
            from tempfile import TemporaryDirectory
            from pathlib import Path
            with TemporaryDirectory() as directory:
                typesafe_name = "TYPE" + "SAFE_API_KEY"
                Path(directory, ".env").write_text(f"openouterkey=one\n{typesafe_name}=two\n", encoding="utf-8")
                providers.load_dotenv(Path(directory))
                self.assertEqual(providers.openrouter_key(), "one")
                self.assertEqual(providers.jev_key(), "two")


if __name__ == "__main__":
    unittest.main()
