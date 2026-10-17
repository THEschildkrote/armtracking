import os
import tempfile
import unittest

from evals.make_fake_log import generate
from evals.replay import safety_audit
from look_intent.memory import TaskMemory, Workspace
from look_intent.scoring import Obj, Params, current_dwell, decide, required_dwell, transition_prior

CUP, SPOON = Obj("c", "cup", 0, 300), Obj("s", "spoon", 150, 300)


def stare(obj, t0, t1, hz=30):
    n = int((t1 - t0) * hz)
    return [(t0 + i / hz, obj.x, obj.y) for i in range(n + 1)]


class Scoring(unittest.TestCase):
    def test_glance_does_not_prehover(self):
        d = decide([CUP, SPOON], stare(CUP, 0.9, 1.0), [], 1.0)
        self.assertEqual(d.object_id, "c")
        self.assertFalse(d.prehover)

    def test_sustained_look_prehovers(self):
        d = decide([CUP, SPOON], stare(CUP, 0.0, 1.0), [], 1.0)
        self.assertTrue(d.prehover)

    def test_history_alone_never_prehovers(self):
        hist = ["spoon", "cup"] * 10 + ["spoon"]
        d = decide([CUP, SPOON], [], hist, 1.0, Params(beta=2.0))
        self.assertFalse(d.prehover)

    def test_habit_lowers_but_floors_dwell(self):
        prior = transition_prior(["spoon", "cup"] * 10 + ["spoon"], ["cup", "spoon"])
        self.assertGreater(prior["cup"], 0.8)
        p = Params(beta=5.0)
        self.assertEqual(required_dwell(prior, "cup", p), p.floor_dwell_s)
        self.assertEqual(required_dwell(prior, "spoon", p), 2 * p.min_dwell_s)

    def test_current_dwell_breaks_on_lookaway(self):
        g = stare(CUP, 0, 0.5) + stare(SPOON, 0.5, 0.6) + stare(CUP, 0.6, 0.8)
        self.assertLess(current_dwell(g, CUP, 0.8), 0.25)


class Memory(unittest.TestCase):
    def test_put_back_checks(self):
        with tempfile.TemporaryDirectory() as d:
            m = TaskMemory(os.path.join(d, "mem.json"))
            m.record_pick(1.0, "c", "cup", 0, 300)
            tgt, why = m.put_back_target("c", [SPOON])
            self.assertEqual((tgt, why), ((0, 300), "ok"))
            tgt, why = m.put_back_target("c", [Obj("b", "block", 10, 310)])
            self.assertIsNone(tgt)
            m.record_pick(2.0, "s", "spoon", 0, 100)  # inside no-go disc
            self.assertIsNone(m.put_back_target("s", [], Workspace(y_min=0))[0])
            self.assertEqual(TaskMemory(m.path).history_classes(), ["cup", "spoon"])

    def test_unknown_object(self):
        self.assertIsNone(TaskMemory().put_back_target("x", [])[0])


class Safety(unittest.TestCase):
    def test_audit_clean_and_dirty(self):
        self.assertEqual(safety_audit(generate(1, 10))["descend_without_authorization"], 0)
        bad = generate(1, 10, inject_violation=True)
        self.assertEqual(safety_audit(bad)["descend_without_authorization"], 1)


if __name__ == "__main__":
    unittest.main()
