import unittest

from hanoi.game import HanoiError, HanoiSession, initial_board


class HanoiGameTests(unittest.TestCase):
    def test_seeded_boards_match_and_default_is_all_a(self):
        self.assertEqual(initial_board(6), {"A": [6, 5, 4, 3, 2, 1], "B": [], "C": []})
        self.assertEqual(initial_board(6, randomize=True, seed=44), initial_board(6, randomize=True, seed=44))
        self.assertNotEqual(initial_board(6, randomize=True, seed=44), initial_board(6, randomize=True, seed=45))

    def test_random_board_is_valid_and_never_trivial_for_supported_sizes(self):
        for disks in range(1, 11):
            source = {"A": list(range(disks, 0, -1)), "B": [], "C": []}
            target = {"A": [], "B": [], "C": list(range(disks, 0, -1))}
            for seed in range(64):
                board = initial_board(disks, randomize=True, seed=seed)
                self.assertNotEqual(board, source, (disks, seed))
                self.assertNotEqual(board, target, (disks, seed))
                self.assertEqual(sorted(disk for stack in board.values() for disk in stack), list(range(1, disks + 1)))
                for stack in board.values():
                    self.assertEqual(stack, sorted(stack, reverse=True))

    def test_legal_moves_and_invalid_stale_action(self):
        session = HanoiSession(3, participants=["one", "two"])
        labels = {item["label"] for item in session.candidates("one")}
        self.assertIn("move:A>B:1", labels)
        self.assertIn("move:A>C:1", labels)
        self.assertIn("post_completion_claim", labels)
        self.assertNotIn("wait", labels)
        accepted = session.act("one", 0, "move_disk", {"from": "A", "to": "B", "disk": 1})
        self.assertEqual(accepted["status"], "accepted")
        stale = session.act("two", 0, "move_disk", {"from": "A", "to": "C", "disk": 2})
        self.assertEqual(stale["status"], "stale")
        blocked = session.act("two", 1, "move_disk", {"from": "A", "to": "B", "disk": 3})
        self.assertEqual(blocked["status"], "rejected")

    def test_snapshot_restores_bounded_candidate_history(self):
        session = HanoiSession(3, participants=["one"])
        session.act("one", 0, "move_disk", {"from": "A", "to": "B", "disk": 1})
        state = session.snapshot("one")
        reverse = state["choice_history"]["move:B>A:1"]
        self.assertTrue(reverse["undoes_last"])
        self.assertEqual(reverse["visits"], 1)
        self.assertIn("reverses the most recent move", state["available_choices"]["move:B>A:1"])
        self.assertEqual(state["recent_moves"][-1]["label"], "move:A>B:1")
        self.assertNotIn("wait", state["available_choices"])

    def test_target_state_explains_claim_and_move_consequences(self):
        session = HanoiSession(1, participants=["one"])
        session.act("one", 0, "move_disk", {"from": "A", "to": "C", "disk": 1})
        state = session.snapshot("one")
        self.assertTrue(state["target_reached"])
        self.assertIn("complete tower", state["available_choices"]["post_completion_claim"])
        self.assertIn("off the completed target tower", state["available_choices"]["move:C>A:1"])

    def test_move_clears_claim_and_target_never_auto_completes(self):
        session = HanoiSession(1, participants=["one", "two"])
        session.act("one", 0, "post_completion_claim", {"work_revision": 0})
        self.assertTrue(session.snapshot("two")["completion"]["claim_open"])
        session.act("two", 1, "move_disk", {"from": "A", "to": "C", "disk": 1})
        current = session.snapshot("one")
        self.assertFalse(current["completion"]["claim_open"])
        self.assertEqual(current["phase"], "solving")
        self.assertTrue(current["target_reached"])

    def test_strict_majority_and_single_solver_self_completion(self):
        single = HanoiSession(2, participants=["only"])
        single.act("only", 0, "post_completion_claim", {"work_revision": 0})
        self.assertTrue(single.completed)
        many = HanoiSession(2, participants=["a", "b", "c", "d"])
        many.act("a", 0, "post_completion_claim", {"work_revision": 0})
        self.assertEqual(many.snapshot("b")["completion"]["quorum"], 3)
        self.assertFalse(many.completed)
        self.assertEqual(many.snapshot("b")["completion"]["approval_count"], 1)

    def test_claim_assessment_lifecycle(self):
        session = HanoiSession(2, participants=["a", "b", "c"])
        session.act("a", 0, "post_completion_claim", {"work_revision": 0})
        claim_round = session.snapshot("b")["completion"]["claim"]["claim_round"]
        session.act("b", 1, "assess_claim", {"work_revision": 0, "claim_round": claim_round, "assessment": "challenge"})
        self.assertFalse(session.completed)
        session.act("c", 2, "assess_claim", {"work_revision": 0, "claim_round": claim_round, "assessment": "endorse"})
        self.assertTrue(session.completed)


if __name__ == "__main__":
    unittest.main()
