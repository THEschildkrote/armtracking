"""Protocol test doubles are confined to tests; production always connects to xAI."""
import asyncio
import json
import threading
import types
import unittest
from unittest.mock import patch
from sponsors.grok import VoiceConfirmation
from sponsors.config import Config

class Socket:
    def __init__(self,events):self.events=iter(events);self.sent=[]
    async def send(self,raw):self.sent.append(json.loads(raw))
    async def recv(self):
        await asyncio.sleep(0)
        return json.dumps(next(self.events))
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False
class Mic:
    def __init__(self,**kwargs):pass
    def __enter__(self):return self
    def __exit__(self,*args):return False

class ProtocolTests(unittest.TestCase):
    def run_case(self,events):
        ws=Socket([{'type':'session.updated'},*events]);callbacks=[]
        sd=types.SimpleNamespace(RawInputStream=Mic)
        with patch.dict('os.environ',{'XAI_API_KEY':'test-only'}),patch.dict('sys.modules',{'sounddevice':sd}),\
             patch('websockets.connect',return_value=ws),patch('sponsors.grok.log') as logger:
            v=VoiceConfirmation(Config())
            def callback(token,result):callbacks.append(result);return True
            asyncio.run(v._run(('m',1,'test'),'mug',callback,threading.Event()))
            return callbacks,logger.call_args.kwargs,ws.sent
    @staticmethod
    def call(result='CONFIRM'):
        return {'type':'response.function_call_arguments.done','name':'confirm_candidate',
                'call_id':'call-test','arguments':json.dumps({'result':result})}
    def test_complete_voice_turn_reaches_gate_and_returns_tool_output(self):
        callbacks,log,sent=self.run_case([{'type':'input_audio_buffer.committed'},self.call(),
            {'type':'response.done','response':{'status':'completed'}}])
        self.assertEqual(callbacks,['CONFIRM']);self.assertTrue(log['accepted'])
        self.assertTrue(any(x.get('item',{}).get('type')=='function_call_output' for x in sent))
        self.assertIsNotNone(log['decision_latency_ms'])
    def test_tool_without_audio_never_calls_gate(self):
        callbacks,log,_=self.run_case([self.call()])
        self.assertEqual(callbacks,[]);self.assertEqual(log['result'],'ERROR')
    def test_conflicting_tools_do_not_confirm(self):
        callbacks,log,_=self.run_case([{'type':'input_audio_buffer.committed'},self.call(),self.call('CANCEL'),
            {'type':'response.done','response':{'status':'completed'}}])
        self.assertEqual(callbacks,['UNCLEAR'])
    def test_failed_response_never_calls_gate(self):
        callbacks,_,_=self.run_case([{'type':'input_audio_buffer.committed'},self.call(),
            {'type':'response.done','response':{'status':'failed'}}])
        self.assertEqual(callbacks,[])
