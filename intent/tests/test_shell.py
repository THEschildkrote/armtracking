import unittest

from evals.replay import safety_audit, ticks
from look_intent.client import IntentService
from look_intent.scoring import Obj, decide
from look_intent.shell import Shell, MockArm


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.arm = MockArm()
        self.shell = Shell(self.arm, IntentService('test', use_chalk=False), self.events.append)
        self.objects = [Obj('c', 'cup', 0, 300), Obj('s', 'spoon', 150, 300)]
        self.shell.scene(self.objects, 0)

    def look(self, n):
        for i in range(n+1):
            self.shell.sample(i/30, 0, 300)

    def test_prehover_does_not_grasp(self):
        self.look(12)
        self.shell.tick(0.4)
        self.assertEqual(self.arm.hover, 'c')
        self.assertEqual(self.arm.grasps, [])
        self.shell.sample(0.41, 0, 300, valid=False)
        self.assertIsNone(self.arm.hover)

    def test_confirmation_once_and_memory(self):
        self.look(30)
        self.assertEqual(self.arm.grasps, ['c'])
        self.assertEqual(safety_audit(self.events)['descend_without_authorization'], 0)
        self.shell.grasp_ok('c', 1.1)
        self.assertEqual(self.shell.memory.history_classes(), ['cup'])

    def test_stale_and_gapped_gaze(self):
        obj = self.objects[0]
        for gaze in [[(0, obj.x, obj.y)], [(0, obj.x, obj.y), (1, obj.x, obj.y)]]:
            self.assertFalse(decide(self.objects, gaze, [], 1).prehover)

    def test_invalid_replay_breaks_dwell(self):
        events = [dict(type='gaze', t=0, x=0, y=300, valid=True),
                  dict(type='gaze', t=0.1, x=0, y=300, valid=False)]
        self.assertEqual(ticks(events, 1.5)[-1].gaze, [])

    def test_reject_incomplete_dwell(self):
        with self.assertRaises(ValueError):
            self.shell.confirm('c', 0)
        self.assertEqual(self.arm.grasps, [])

    def test_voice_and_stale_prediction(self):
        self.shell.confirm('s', 0.1, voice=True)
        self.assertEqual(self.arm.grasps, ['s'])
        self.assertEqual(safety_audit(self.events)['descend_without_authorization'], 0)
        self.look(12)
        self.shell.tick(0.4, clock=lambda: 1.0)
        self.assertIsNone(self.arm.hover)

class ServiceTests(unittest.TestCase):
    def test_custom_chalk_params_are_not_silently_ignored(self):
        from look_intent.scoring import Params
        with self.assertRaises(ValueError):
            IntentService('test', params=Params(beta=0))

    def test_timeout_does_not_accumulate_queries(self):
        import concurrent.futures as cf
        from unittest.mock import Mock, patch
        service = IntentService('test', use_chalk=False)
        future = cf.Future()
        future.set_running_or_notify_cancel()
        service._client = object()
        with patch.object(service._pool, 'submit', return_value=future) as submit:
            service.timeout_s = 0.001
            service.predict([], [], [], 1)
            service._chalk_down_until = 0
            service.predict([], [], [], 2)
            self.assertEqual(submit.call_count, 1)
        service.close()
