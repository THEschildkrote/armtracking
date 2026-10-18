import asyncio
import csv
from dataclasses import replace
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'intent'))
from look_routine.model import statistics,prior,validate_suggestions
from look_intent.client import IntentService
from look_intent.shell import MockArm
from look_intent.scoring import Obj
from sponsors.bridge import SponsorShell
from sponsors.config import Config
from sponsors.chalk_client import RoutineClient,rank
from sponsors.grok import parse_result,VoiceConfirmation,api_key

class RoutineTests(unittest.TestCase):
    def test_windows_users_cancels_and_future(self):
        now=datetime(2026,10,17,13,tzinfo=timezone.utc)
        def row(days,label='mug',status='confirmed',user='demo'):
            return dict(timestamp=(now-timedelta(days=days)).isoformat(),user_id=user,object_label=label,status=status)
        data=[row(1),row(10),row(15),row(-1),row(2,status='cancelled'),row(2,user='other')]
        data.append(data[0].copy())
        s=statistics(data,'demo',now.isoformat(),9,['mug','water_bottle'])
        self.assertEqual(s['counts_7d']['mug'],1);self.assertEqual(s['counts_14d']['mug'],2)
        self.assertEqual(s['hour_counts']['mug'],2)
        self.assertEqual(prior(s['hour_counts']),{'mug':.75,'water_bottle':.25})
    def test_ranking_changes_with_time_prior_not_gaze(self):
        c=[('a','mug',.5),('b','water_bottle',.5)]
        self.assertEqual(rank(c,{'mug':.8,'water_bottle':.2},.7)[0]['id'],'a')
        self.assertEqual(rank(c,{'mug':.2,'water_bottle':.8},.7)[0]['id'],'b')
        self.assertEqual(rank(c,None,.7)[0]['score'],.5)
        self.assertEqual(rank(c,{'mug':.2,'water_bottle':.8},0)[0]['score'],.5)
    def test_reject_bad_agent_data(self):
        now=datetime.now(timezone.utc)
        d={'schema_version':1,'user_id':'demo','generated_at':now.isoformat(),'hour_priors':{'9':{'mug':float('nan')}}}
        with self.assertRaises(ValueError):validate_suggestions(d,'demo',now.isoformat())
        d['hour_priors']['9']={'mug':1.};d['generated_at']=(now-timedelta(days=3)).isoformat()
        with self.assertRaises(ValueError):validate_suggestions(d,'demo',now.isoformat())
    def test_chalk_timeout_no_queue_accumulation(self):
        c=RoutineClient(Config());c.config=replace(c.config,chalk_enabled=True,chalk_timeout_s=.01);c.client=object()
        try:
            with patch.object(c,'_query',side_effect=lambda *a:time.sleep(.1)) as query,patch('sponsors.chalk_client.log'):
                started=time.monotonic();self.assertIsNone(c.query(['mug','phone']))
                self.assertLess(time.monotonic()-started,.08)
                self.assertIsNone(c.query(['mug','phone']));self.assertEqual(query.call_count,1)
        finally:c.close()
    def test_startup_warmup_uses_separate_budget(self):
        c=RoutineClient(Config());c.config=replace(c.config,chalk_enabled=True);c.client=object()
        try:
            with patch.object(c,'_query',return_value={'mug':1.}) as query,patch('sponsors.chalk_client.log') as logger:
                c.warmup(['mug'],wait=True)
                self.assertEqual(query.call_args.kwargs['timeout_s'],10)
                self.assertEqual(c.config.chalk_timeout_s,.5)
                self.assertEqual(logger.call_args.kwargs['phase'],'startup')
        finally:c.close()

    def test_strict_demo_never_fakes_success(self):
        c=RoutineClient(Config())
        try:
            with self.assertRaises(RuntimeError):c.query(['mug'],strict=True)
        finally:c.close()
    def test_resolvers_execute_under_real_sdk(self):
        import routine_features as r
        now=datetime.now(timezone.utc).isoformat()
        loaded=r.load_routine_log('[]')
        counts=r.routine_counts(loaded,'demo',now,9,'["mug","water_bottle"]','America/New_York')
        probs=json.loads(r.routine_probability(counts.hour_counts_json,'{}',0.))
        self.assertGreater(probs['mug'],probs['water_bottle'])

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.service=IntentService('demo',use_chalk=False);self.arm=MockArm();self.events=[]
        self.shell=SponsorShell(self.arm,self.service,self.events.append,config=Config())
        self.now=time.monotonic();self.shell.scene([Obj('m','mug',0,0),Obj('b','water_bottle',120,0)],self.now-2)
        for i in range(31):self.shell.sample(self.now-1+i/30,0,0)
        self.token=('m',self.shell.scene_revision,'test');self.shell.voice_token=self.token
        self.patch=patch('sponsors.bridge.grab_event');self.patch.start()
    def tearDown(self):self.patch.stop();self.service.close()
    def test_blink_still_works_when_disabled(self):
        self.assertEqual(self.shell.blink_confirm(self.now,0,0),'m');self.assertEqual(self.arm.grasps,['m'])
    def test_voice_uses_existing_gate_and_audit(self):
        with patch('sponsors.bridge.time.monotonic',return_value=self.now):
            self.assertTrue(self.shell.voice_result(self.token,'CONFIRM'))
        self.assertEqual(self.arm.grasps,['m'])
        self.assertEqual([e['method'] for e in self.events if e['type']=='fixation_fired'],['grok_voice'])
    def test_simultaneous_voice_and_blink_dispatch_once(self):
        barrier=threading.Barrier(2)
        def voice():barrier.wait();self.shell.voice_result(self.token,'CONFIRM')
        def blink():barrier.wait();self.shell.blink_confirm(time.monotonic(),0,0)
        a=threading.Thread(target=voice);b=threading.Thread(target=blink);a.start();b.start();a.join();b.join()
        self.assertEqual(self.arm.grasps,['m'])
    def test_cancel_then_late_voice(self):
        self.shell.cancel_all(self.now)
        self.assertFalse(self.shell.voice_result(self.token,'CONFIRM'));self.assertEqual(self.arm.grasps,[])
    def test_scene_change_rejects_voice(self):
        self.shell.scene([Obj('m','mug',100,0)],self.now)
        self.assertFalse(self.shell.voice_result(self.token,'CONFIRM'));self.assertEqual(self.arm.grasps,[])
    def test_looking_away_rejects_voice(self):
        self.shell.sample(self.now+.01,500,500)
        self.assertFalse(self.shell.voice_result(self.token,'CONFIRM'))
    def test_tracking_loss_rejects_voice(self):
        self.shell.sample(self.now+.01,float('nan'),float('nan'),False)
        self.assertFalse(self.shell.voice_result(self.token,'CONFIRM'))
    def test_unclear_never_grabs(self):
        self.assertFalse(self.shell.voice_result(self.token,'UNCLEAR'));self.assertEqual(self.arm.grasps,[])
    def test_expired_voice_does_not_grab(self):
        with patch('sponsors.bridge.time.monotonic',return_value=self.now+3):
            self.assertFalse(self.shell.voice_result(self.token,'CONFIRM'))
    def test_duplicate_voice_does_not_grab_twice(self):
        with patch('sponsors.bridge.time.monotonic',return_value=self.now):
            self.assertTrue(self.shell.voice_result(self.token,'CONFIRM'))
            self.assertFalse(self.shell.voice_result(self.token,'CONFIRM'))

class VoiceTests(unittest.TestCase):
    def test_explicit_key_file_supports_env_or_raw_without_execution(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{},clear=True):
            path=Path(folder)/'secret';config=replace(Config(),xai_key_file=str(path))
            for content in ('test-key', 'export XAI_API_KEY = "test-key" # local'):
                path.write_text(content);self.assertEqual(api_key(config),'test-key')
            path.write_text('OTHER_KEY=test-key');self.assertIsNone(api_key(config))

    def test_tool_schema_is_strict(self):
        self.assertEqual(parse_result({'name':'confirm_candidate','arguments':'{"result":"CONFIRM"}'}),'CONFIRM')
        for args in ['{"result":"CONFIRM","object":"other"}','{"result":"yes"}','null']:
            with self.assertRaises((ValueError,TypeError)):parse_result({'name':'confirm_candidate','arguments':args})
    def test_missing_key_logs_failure_and_leaves_blink(self):
        voice=VoiceConfirmation(Config())
        with patch.dict('os.environ',{},clear=True),patch('sponsors.grok.log') as log:
            asyncio.run(voice._run(('m',0,'x'),'mug',lambda *a:self.fail('Must not authorize'),threading.Event()))
            self.assertFalse(log.call_args.kwargs['api_attempted'])
            self.assertEqual(log.call_args.kwargs['result'],'ERROR')

if __name__=='__main__':unittest.main()
