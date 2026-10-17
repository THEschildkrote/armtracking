import asyncio
import json
import unittest
from collections import deque
from unittest.mock import patch

from look_intent.client import IntentService
from look_intent.grok_voice import GrokVoice, session_update
from look_intent.scoring import Obj
from look_intent.shell import MockArm, Shell
from look_intent.voice import VoiceBridge


class Audio:
    def __init__(self):
        self.queue=asyncio.Queue()
        self.listening=False
        self.opened=self.closed=False
        self.listen_history=[]
    async def open(self):
        self.opened=True
    def listen(self,enabled):
        self.listening=enabled
        self.listen_history.append(enabled)
    async def play(self,pcm):
        self.listen(False)
    async def close(self):
        self.closed=True
        self.listen(False)


class Socket:
    def __init__(self,events):
        self.events=deque(events)
        self.sent=[]
    async def __aenter__(self):
        return self
    async def __aexit__(self,*args):
        pass
    async def send(self,message):
        self.sent.append(json.loads(message))
    async def recv(self):
        await asyncio.sleep(0)
        if not self.events:
            raise RuntimeError('Test protocol exhausted')
        return json.dumps(self.events.popleft())


def call(name='select',id='a',**args):
    return dict(type='response.function_call_arguments.done',name=name,call_id=id,
                arguments=json.dumps(args))


class GrokTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service=IntentService('test',use_chalk=False)
        self.addCleanup(self.service.close)
        self.shell=Shell(MockArm(),self.service,lambda e:None)
        self.shell.scene([Obj('c','cup',0,300),Obj('s','spoon',50,300)],0)
        self.bridge=VoiceBridge(self.shell)
        self.audio=Audio()

    def agent(self,events):
        self.ws=Socket(events)
        return GrokVoice(self.bridge,clock=lambda:1.1,
                         connect=lambda *a,**kw:self.ws,audio_factory=lambda:self.audio)

    async def test_idle_never_opens_audio_or_network(self):
        agent=self.agent([])
        self.assertFalse(await agent.answer_question())
        self.assertFalse(self.audio.opened)

    async def test_real_wire_flow_one_action_and_one_continuation(self):
        self.shell.request_voice(1,suggested_id='c')
        agent=self.agent([dict(type='session.updated'),dict(type='response.done'),
            dict(type='input_audio_buffer.committed'),
            call(object_id='s',correction=True),call(name='cancel',id='b'),
            dict(type='response.done'),dict(type='response.done')])
        with patch.dict('os.environ',XAI_API_KEY='test-only'):
            self.assertTrue(await agent.answer_question())
        self.assertEqual(self.shell.arm.grasps,['s'])
        outputs=[e for e in self.ws.sent if e['type']=='conversation.item.create'
                 and e['item']['type']=='function_call_output']
        self.assertEqual(len(outputs),2)
        self.assertTrue(json.loads(outputs[0]['item']['output'])['ok'])
        self.assertFalse(json.loads(outputs[1]['item']['output'])['ok'])
        self.assertEqual(sum(e['type']=='response.create' for e in self.ws.sent),2)
        self.assertIn(True,self.audio.listen_history)
        self.assertTrue(self.audio.closed)

    async def test_tools_before_human_speech_do_not_move_arm(self):
        self.shell.request_voice(1,suggested_id='c')
        agent=self.agent([dict(type='session.updated'),call(object_id='c'),
                          dict(type='response.done'),dict(type='error')])
        with patch.dict('os.environ',XAI_API_KEY='test-only'):
            with self.assertRaises(RuntimeError):
                await agent.answer_question()
        self.assertEqual(self.shell.arm.grasps,[])
        self.assertTrue(self.audio.closed)
        self.assertIsNone(self.shell.pending_voice)

    def test_tool_schema_and_codec(self):
        config=session_update()['session']
        self.assertEqual({t['name'] for t in config['tools']},
                         {'select','shift','cancel','release','put_back','repeat_last'})
        self.assertEqual(config['audio']['input']['format']['rate'],24000)
