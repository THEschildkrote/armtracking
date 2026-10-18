"""The studio animation follows a confirmed request and never drives hardware."""
import copy
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from sponsors.config import Config
from sponsors.showcase import server


class ShowcaseFlowTests(unittest.TestCase):
    def setUp(self):
        self.original_state=copy.deepcopy(server.STATE)
        server.STATE.update(busy=False,context_busy=False,context_error=None,events=[],rows=[],hour=9,selected=None,
                            arm='idle',animation_id=None,completed_animation_id=None,
                            grok=None,cloud=None,evaluation=None)
        self.client=Mock()
        self.client.query.return_value={'mug':.1,'water_bottle':.2,'phone':.3,'medication':.4}
        self.client.last={'result':'success','query_id':'test-query'}
        self.patches=[patch.object(server,'CONFIG',Config(),create=True),
                      patch.object(server,'CLIENT',self.client,create=True),
                      patch.object(server,'log')]
        for item in self.patches:item.start()

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        server.STATE.clear();server.STATE.update(self.original_state)

    def test_all_four_objects_can_be_selected_including_phone_and_medicine(self):
        for label in server.LABELS:
            server.action('select',{'label':label})
            self.assertEqual(server.STATE['selected'],label)
            self.assertEqual(server.STATE['arm'],'awaiting confirmation')
            self.assertIsNone(server.STATE['animation_id'])
        self.client.query.assert_not_called()

    def test_confirm_requires_selection_and_does_not_finish_until_animation_callback(self):
        with self.assertRaises(ValueError):server.action('confirm',{})
        server.action('select',{'label':'phone'})
        with patch.object(server.time,'sleep',side_effect=AssertionError('No simulated delay')):
            server.action('confirm',{})
        nonce=server.STATE['animation_id']
        self.assertIsInstance(nonce,str)
        self.assertEqual(server.STATE['arm'],'grabbing')
        self.assertFalse(any(e['message']=='Arm animation completed' for e in server.STATE['events']))
        with self.assertRaises(ValueError):server.action('confirm',{})
        with self.assertRaises(ValueError):server.action('select',{'label':'mug'})
        with self.assertRaises(ValueError):server.action('rank',{'hour':15})
        self.assertEqual(server.STATE['selected'],'phone')
        self.assertEqual(server.STATE['animation_id'],nonce)
        server.action('animation_complete',{'animation_id':nonce})
        self.assertEqual(server.STATE['arm'],'delivered')
        self.assertEqual(server.STATE['completed_animation_id'],nonce)
        self.client.query.assert_not_called()

    def test_stale_and_duplicate_callbacks_never_finish_a_different_request(self):
        server.action('select',{'label':'mug'});server.action('confirm',{})
        old=server.STATE['animation_id']
        for invalid in (None,'wrong',False,{}):
            server.action('animation_complete',{'animation_id':invalid})
            self.assertEqual(server.STATE['arm'],'grabbing')
        server.action('animation_complete',{'animation_id':old})
        event_count=len(server.STATE['events'])
        server.action('animation_complete',{'animation_id':old})
        self.assertEqual(len(server.STATE['events']),event_count)
        server.action('select',{'label':'medication'})
        self.assertIsNone(server.STATE['completed_animation_id'])
        server.action('confirm',{})
        current=server.STATE['animation_id']
        self.assertNotEqual(old,current)
        server.action('animation_complete',{'animation_id':old})
        self.assertEqual(server.STATE['arm'],'grabbing')
        self.assertEqual(server.STATE['animation_id'],current)
        server.action('animation_complete',{'animation_id':current})
        self.assertEqual(server.STATE['arm'],'delivered')
        completions=[e for e in server.STATE['events'] if e['message']=='Arm animation completed']
        self.assertEqual([e['animation_id'] for e in completions],[old,current])

    def test_rank_all_hours_and_all_candidates_use_the_live_client_contract(self):
        for hour in (0,10,23):
            server.action('rank',{'hour':hour,'profile':'new-user'})
            self.client.query.assert_called_with(list(server.LABELS),hour=hour,strict=True)
            self.assertEqual({r['label'] for r in server.STATE['rows']},set(server.LABELS))
            self.assertTrue(all(r['gaze']==.25 for r in server.STATE['rows']))
            self.assertEqual(server.STATE['rows'][0]['label'],'medication')
            self.assertEqual(server.STATE['profile'],'new-user')
        self.assertEqual(self.client.query.call_count,3)

    def test_rank_rejects_coercion_and_out_of_range_hours(self):
        for hour in (True,False,'9',9.0,None,-1,24):
            with self.subTest(hour=hour),self.assertRaises(ValueError):
                server.action('rank',{'hour':hour})
        self.client.query.assert_not_called()

    def test_context_query_does_not_block_selection_or_finish_its_animation(self):
        entered=threading.Event();finish=threading.Event()
        priors=self.client.query.return_value
        def delayed(*args,**kwargs):
            entered.set()
            if not finish.wait(2):raise RuntimeError('Test did not release query')
            return priors
        self.client.query.side_effect=delayed
        try:
            self.assertTrue(server.start_context({'hour':18,'profile':'new-user'}))
            self.assertTrue(entered.wait(1))
            self.assertTrue(server.STATE['context_busy'])
            self.assertFalse(server.STATE['busy'])
            self.assertTrue(server.LOCK.acquire(False));server.LOCK.release()
            self.assertFalse(server.start_context({'hour':19,'profile':'demo'}))
            server.action('select',{'label':'phone'});server.action('confirm',{})
            nonce=server.STATE['animation_id']
            self.assertEqual(server.STATE['arm'],'grabbing')
        finally:finish.set()
        self.assertTrue(server.CONTEXT_LOCK.acquire(timeout=1));server.CONTEXT_LOCK.release()
        self.assertFalse(server.STATE['context_busy'])
        self.assertFalse(server.STATE['busy'])
        self.assertEqual(server.STATE['selected'],'phone')
        self.assertEqual(server.STATE['arm'],'grabbing')
        self.assertEqual(server.STATE['animation_id'],nonce)
        self.assertEqual(server.STATE['hour'],18)
        self.assertEqual(server.STATE['cloud']['query_id'],'test-query')
        self.assertEqual(len(server.STATE['rows']),4)
        self.client.query.assert_called_once_with(list(server.LABELS),hour=18,strict=True)

    def test_context_failure_preserves_control_state_and_previous_context(self):
        server.STATE.update(busy=True,error='Existing control status',selected='medication',
                            arm='grabbing',animation_id='current',cloud={'query_id':'previous'},
                            rows=[{'label':'mug'}],hour=9,profile='demo')
        self.client.query.side_effect=RuntimeError('Provider response must stay private')
        self.assertTrue(server.start_context({'hour':15,'profile':'new-user'}))
        self.assertTrue(server.CONTEXT_LOCK.acquire(timeout=1));server.CONTEXT_LOCK.release()
        self.assertEqual(server.STATE['error'],'Existing control status')
        self.assertTrue(server.STATE['busy'])
        self.assertEqual(server.STATE['selected'],'medication')
        self.assertEqual(server.STATE['animation_id'],'current')
        self.assertEqual(server.STATE['arm'],'grabbing')
        self.assertEqual(server.STATE['cloud'],{'query_id':'previous'})
        self.assertEqual(server.STATE['rows'],[{'label':'mug'}])
        self.assertEqual(server.STATE['hour'],9)
        self.assertEqual(server.STATE['profile'],'demo')
        self.assertEqual(server.STATE['context_error'],'Routine context unavailable; previous context kept.')
        self.assertNotIn('Provider response',json.dumps(server.STATE))

    def test_context_validation_uses_strict_hours_and_profiles(self):
        for body in ({'hour':False},{'hour':'15'},{'hour':15.0},{'hour':24},{'hour':-1},
                     {'hour':15,'profile':'unrecognized'}):
            with self.subTest(body=body),self.assertRaises(ValueError):server.start_context(body)
        self.client.query.assert_not_called()
        self.assertFalse(server.STATE['context_busy'])

    def test_context_http_route_validates_origin_and_body_without_control_lock(self):
        def request(payload,origin='http://127.0.0.1:8765'):
            handler=object.__new__(server.Handler);handler.path='/api/context'
            handler.headers={'Origin':origin,'Content-Length':str(len(payload))}
            handler.rfile=BytesIO(payload);handler.send=Mock()
            handler.do_POST();return handler.send.call_args.args[1]
        with patch.object(server,'PORT',8765,create=True), \
                patch.object(server,'LOCK') as control_lock:
            control_lock.acquire.side_effect=AssertionError('Context cannot take control lock')
            self.assertEqual(request(b'{}','https://external.example'),403)
            for payload in (b'[1]',b'null',b'{broken',b'{"hour":"15"}'):
                self.assertEqual(request(payload),400)
            with patch.object(server,'start_context',return_value=False):
                self.assertEqual(request(b'{"hour":15}'),409)
            self.client.query.assert_not_called()

    def test_latest_valid_saved_evaluation_is_loaded_without_running_services(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(server,'ROOT',Path(folder)), \
                patch('sponsors.evaluation.run',side_effect=AssertionError('Must stay read-only')), \
                patch('sponsors.factory_agent.run',side_effect=AssertionError('Must stay read-only')):
            reports=Path(folder)/'logs/evaluations'
            def saved(name,value,modified):
                path=reports/name/'evaluation.json';path.parent.mkdir(parents=True)
                path.write_text(value if isinstance(value,str) else json.dumps(value))
                os.utime(path,ns=(modified,modified))
            def report(run):
                return {'run_id':run,'text':'Measured activity benchmark','report':{'results':{'hour-aware':{'accuracy':.15}}}}
            saved('old',report('old'),1)
            saved('latest',report('latest'),2)
            saved('invalid','{broken',3)
            saved('failed',{**report('failed'),'failed':True},4)
            saved('incomplete',{'run_id':'unfinished','text':'Waiting'},5)
            server.evidence()
            self.assertEqual(server.STATE['evaluation']['run_id'],'latest')
            self.assertEqual(server.STATE['events'],[])
            self.client.query.assert_not_called()

    def test_invalid_reports_do_not_replace_an_existing_result(self):
        existing={'run_id':'kept'};server.STATE['evaluation']=existing
        with tempfile.TemporaryDirectory() as folder,patch.object(server,'ROOT',Path(folder)):
            path=Path(folder)/'logs/evaluations/bad/evaluation.json'
            path.parent.mkdir(parents=True);path.write_text('[]')
            server.evidence()
        self.assertIs(server.STATE['evaluation'],existing)


if __name__=='__main__':unittest.main()
