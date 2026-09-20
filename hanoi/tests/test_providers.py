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
