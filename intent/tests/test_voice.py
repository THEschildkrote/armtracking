import json
import tempfile
import unittest
from pathlib import Path

from evals.replay import Tick, episode_metrics, safety_audit, ticks
from look_intent.client import IntentService
from look_intent.feedback import FeedbackMemory
from look_intent.scoring import Decision, Obj, transition_prior
from look_intent.shell import MockArm, Shell
from look_intent.voice import VoiceBridge

CUP, SPOON = Obj('c', 'cup', 0, 300), Obj('s', 'spoon', 50, 300)


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.service = IntentService('test', use_chalk=False, log=self.events.append)
        self.addCleanup(self.service.close)
        self.shell = Shell(MockArm(), self.service, self.events.append)
        self.shell.scene([CUP, SPOON], 0)
        self.bridge = VoiceBridge(self.shell)

    def question(self, t=1):
        return self.shell.request_voice(t, suggested_id='c')['question_id']

    def call(self, name, args=None, q=None, call='a', t=1.1):
        return self.bridge.execute(name, args or {}, call, q or self.question(), t)

    def test_correction_changes_later_prior_without_faking_pick(self):
        self.shell.memory.record_pick(0, 'b', 'block', 150, 300)
        before = transition_prior(['block'], ['cup', 'spoon'])
        q = self.question()
        result = self.call('select', dict(object_id='s', correction=True), q)
        self.assertTrue(result['ok'])
        self.assertEqual(self.shell.memory.history_classes(), ['block'])
        after = transition_prior(['block'], ['cup', 'spoon'], corrections=self.shell.feedback.snapshot())
        self.assertGreater(after['spoon'], before['spoon'])
        self.assertEqual(sum(e['type']=='voice_correction' for e in self.events), 1)
        self.assertEqual(safety_audit(self.events)['descend_without_authorization'], 0)
        self.assertEqual(self.shell.arm.grasps, ['s'])
        self.assertEqual(self.call('select', dict(object_id='s', correction=True), q), result)
        self.assertEqual(self.shell.arm.grasps, ['s'])

    def test_correction_is_passed_into_next_query(self):
        self.shell.memory.record_pick(0, 'b', 'block', 150, 300)
        self.call('select', dict(object_id='s', correction=True))
        self.shell.tick(1.2)
        query = [e for e in self.events if e['type']=='intent_query'][-1]
        self.assertEqual(query['correction_count'], 1)

    def test_stale_question_and_invalid_args_do_nothing(self):
        q = self.question()
        for args in [dict(object_id='missing'), dict(object_id='s', correction='yes'),
                     dict(object_id='s', motor_angle=50)]:
            self.assertFalse(self.call('select', args, q, call=str(args))['ok'])
        self.assertFalse(self.call('select', dict(object_id='s'), q, t=20)['ok'])
        self.assertEqual(self.shell.arm.grasps, [])
        self.shell.scene([CUP], 2)
        self.assertFalse(self.call('select', dict(object_id='c'), q, call='changed', t=2.1)['ok'])

    def test_ambiguous_gaze_opens_gate_and_cannot_descend(self):
        for i in range(31):
            self.shell.sample(i/30, 25, 300)
        self.assertIsNotNone(self.shell.pending_voice)
        self.assertEqual(self.shell.arm.grasps, [])
        self.shell.tick(1)
        self.assertIsNone(self.shell.arm.hover)

    def test_cancel_closes_question_and_stops(self):
        q = self.question()
        self.assertTrue(self.call('cancel', q=q)['ok'])
        self.assertGreater(self.shell.arm.stops, 0)
        self.assertIsNone(self.shell.pending_voice)
        self.assertFalse(self.call('select', dict(object_id='c'), q, call='late')['ok'])

    def test_shift_and_repeat(self):
        self.assertTrue(self.call('shift', dict(direction='right'))['ok'])
        self.assertEqual(self.shell.arm.grasps, ['s'])
        self.shell.grasp_ok('s', 1.2)
        self.shell.delivered('s', 1.3)
        self.shell.released('s', 1.4)
        q = self.question(2)
        self.assertTrue(self.call('repeat_last', q=q, t=2.1)['ok'])
        self.assertEqual(self.shell.arm.grasps, ['s', 's'])

    def held(self):
        self.shell.confirm('c', 0.5, voice=True)
        self.shell.grasp_ok('c', 0.6)
        self.shell.delivered('c', 0.7)

    def test_putback_geometry_and_release_callbacks(self):
        self.held()
        self.shell.scene([Obj('b', 'block', 5, 300)], 0.8)
        q = self.question()
        self.assertFalse(self.call('put_back', dict(object_id='c'), q)['ok'])
        self.assertEqual(self.shell.arm.returns, [])
        self.assertTrue(self.call('release', q=q, call='release')['ok'])
        self.assertEqual(self.shell.held_id, 'c')
        self.shell.released('c', 1.2)
        self.assertIsNone(self.shell.held_id)

    def test_putback_success(self):
        self.held()
        self.shell.scene([Obj('b', 'block', 150, 300)], 0.8)
        self.assertTrue(self.call('put_back', dict(object_id='c'))['ok'])
        self.assertEqual(self.shell.arm.returns, [('c', 0, 300)])

    def test_wire_event_and_malformed_json(self):
        q = self.question()
        event = dict(type='response.function_call_arguments.done', name='select',
                     call_id='wire', arguments='{')
        output = self.bridge.handle_event(event, q, 1.1)
        self.assertFalse(json.loads(output['item']['output'])['ok'])
        self.assertEqual(output['item']['call_id'], 'wire')

    def test_user_scoped_persistence(self):
        self.call('select', dict(object_id='s', correction=True))
        correction = self.shell.feedback.corrections[0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'feedback.json'
            memory = FeedbackMemory('test', path)
            self.assertTrue(memory.record(correction))
            self.assertFalse(memory.record(correction))
            self.assertEqual(len(FeedbackMemory('test', path).snapshot()), 1)
            with self.assertRaises(ValueError):
                FeedbackMemory('other', path)

    def test_replay_uses_only_past_corrections(self):
        self.shell.memory.record_pick(0, 'b', 'block', 150, 300)
        self.call('select', dict(object_id='s', correction=True))
        correction = next(e for e in self.events if e['type']=='voice_correction')
        event = dict(correction, t=0.1)
        replay = ticks([dict(type='scene', t=0, objects=[CUP.__dict__, SPOON.__dict__]),
                        event, dict(type='end', t=0.3)], 1.5)
        self.assertEqual(replay[0].corrections, [])
        self.assertEqual(len(replay[-1].corrections), 1)

    def test_corrected_guess_is_false_even_if_old_fixation_matches(self):
        tk = [Tick(0, [CUP,SPOON], [], []), Tick(0.5, [CUP,SPOON], [], [])]
        events = [dict(type='fixation_fired', t=0.6, object_id='c'),
                  dict(type='voice_correction', t=0.7, wrong_id='c', correct_id='s')]
        metric = episode_metrics(events, tk, [Decision('c', 1, True)]*2)
        self.assertEqual(metric['false_prehover'], 1)
        self.assertEqual(metric['wrong_predictions_at_correction'], 1)

    def test_authorization_expires_and_cannot_be_reused(self):
        events = [dict(type='voice_select', t=0, object_id='c', task_id='one'),
                  dict(type='arm_descend', t=0.1, object_id='c', task_id='one'),
                  dict(type='arm_descend', t=0.2, object_id='c', task_id='one')]
        self.assertEqual(safety_audit(events)['descend_without_authorization'], 1)
        events[1]['t'] = 3
        self.assertEqual(safety_audit(events)['descend_without_authorization'], 2)

    def test_detector_jitter_does_not_invalidate_question(self):
        q=self.question()
        self.shell.scene([Obj('c','cup',2,301),Obj('s','spoon',52,301)],1.05)
        self.assertEqual(self.shell.pending_voice['question_id'],q)
        self.assertTrue(self.call('select',dict(object_id='s'),q)['ok'])
