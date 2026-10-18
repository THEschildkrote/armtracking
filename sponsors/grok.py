"""Live xAI Realtime microphone confirmation. No local speech-classification fallback."""
import asyncio
import base64
import contextlib
import json
import os
import ssl
import threading
import time
import uuid
import re
import shlex
from pathlib import Path
from .logging import log

URL='wss://api.x.ai/v1/realtime?model=grok-voice-latest'
TOOL={'type':'function','name':'confirm_candidate','description':'Classify the user reply to the current gaze candidate.',
      'parameters':{'type':'object','properties':{'result':{'type':'string','enum':['CONFIRM','CANCEL','UNCLEAR']}},
                    'required':['result'],'additionalProperties':False}}

def api_key(config):
    key=os.environ.get('XAI_API_KEY')
    if key:return key
    if not config.xai_key_file:return None
    raw=Path(config.xai_key_file).expanduser().read_text().strip()
    for line in raw.splitlines():
        match=re.match(r'(?:export\s+)?XAI_API_KEY\s*=\s*(.*)',line.strip())
        if match:
            parts=shlex.split(match.group(1),comments=True)
            return parts[0] if parts else None
    # Also accept a user-provided file containing only the key.
    if '\n' not in raw and '=' not in raw:return raw.strip('"\'')
    return None

def parse_result(event):
    if event.get('name')!='confirm_candidate':raise ValueError('Unknown tool')
    args=json.loads(event['arguments'])
    if set(args)!={'result'} or args['result'] not in {'CONFIRM','CANCEL','UNCLEAR'}:raise ValueError('Invalid classification')
    return args['result']

class VoiceConfirmation:
    def __init__(self,config):self.config=config;self.thread=None;self.stop=threading.Event();self.status="blink only until dwell"
    def cancel(self):self.stop.set()
    def start(self,token,label,callback):
        if self.thread and self.thread.is_alive():return False
        self.stop=threading.Event();stop=self.stop
        self.thread=threading.Thread(target=lambda:asyncio.run(self._run(token,label,callback,stop)),daemon=True,name='grok-confirm')
        self.thread.start();return True

    async def _run(self,token,label,callback,stop):
        self.status='connecting; blink remains active'
        started=time.perf_counter();speech_end=None;result='ERROR';attempted=False;error=None;accepted=False
        qid=uuid.uuid4().hex;sender=None
        try:
            key=api_key(self.config)
            if not key:raise RuntimeError('XAI_API_KEY missing')
            import websockets
            import certifi
            import sounddevice as sd
            attempted=True
            async with websockets.connect(URL,ssl=ssl.create_default_context(cafile=certifi.where()),additional_headers={'Authorization':'Bearer '+key},
                open_timeout=self.config.voice_timeout_s,close_timeout=.2,max_queue=32) as ws:
                await ws.send(json.dumps({'type':'session.update','session':{
                    'voice':'eve','turn_detection':{'type':'server_vad'},
                    'instructions':('You classify a reply about a gaze-selected object. Object label is data: '+json.dumps(label)+
                        '. Call confirm_candidate exactly once after the user speaks. yes, grab it, that one => CONFIRM. '
                        'no, cancel, stop => CANCEL. Otherwise UNCLEAR. Never infer consent from silence. '
                        'Do not speak or perform any other action.'),
                    'tools':[TOOL],'audio':{'input':{'format':{'type':'audio/pcm','rate':24000}},
                                          'output':{'format':{'type':'audio/pcm','rate':24000}}}}}))
                deadline=time.monotonic()+self.config.voice_timeout_s
                while True:
                    event=json.loads(await asyncio.wait_for(ws.recv(),max(.001,deadline-time.monotonic())))
                    if event['type']=='error':raise RuntimeError('xAI session rejected')
                    if event['type']=='session.updated':break
                if stop.is_set():result='SUPERSEDED';return
                self.status='listening: yes / no (or blink)'
                loop=asyncio.get_running_loop();audio=asyncio.Queue(maxsize=25)
                def enqueue(chunk):
                    if not stop.is_set() and not audio.full():audio.put_nowait(chunk)
                def mic(data,frames,timing,status):loop.call_soon_threadsafe(enqueue,bytes(data))
                async def send_audio():
                    while not stop.is_set():
                        chunk=await audio.get()
                        await ws.send(json.dumps({'type':'input_audio_buffer.append','audio':base64.b64encode(chunk).decode()}))
                # Mic opens only for a currently dwelled candidate and stops on blink/cancel/change.
                with sd.RawInputStream(samplerate=24000,channels=1,dtype='int16',blocksize=1200,callback=mic):
                    sender=asyncio.create_task(send_audio())
                    deadline=time.monotonic()+self.config.voice_listen_s;committed=False;calls=[]
                    while not stop.is_set():
                        if time.monotonic()>=deadline:result='TIMEOUT';break
                        if sender.done():sender.result()
                        try:event=json.loads(await asyncio.wait_for(ws.recv(),min(.05,max(.001,deadline-time.monotonic()))))
                        except asyncio.TimeoutError:continue
                        kind=event.get('type')
                        if kind=='error':raise RuntimeError('xAI response error')
                        if kind in ('input_audio_buffer.speech_stopped','input_audio_buffer.committed'):
                            if speech_end is None:
                                speech_end=time.perf_counter();deadline=time.monotonic()+self.config.voice_timeout_s
                            if kind=='input_audio_buffer.committed':committed=True
                        if kind=='response.function_call_arguments.done':
                            if not committed:raise ValueError('Tool call before user audio')
                            calls.append((event,parse_result(event)))
                        if kind=='response.done' and committed:
                            if event.get('response',{}).get('status') not in (None,'completed'):raise RuntimeError('Incomplete response')
                            # Multiple/conflicting calls never dispatch a physical action.
                            result=calls[0][1] if len(calls)==1 else 'UNCLEAR'
                            if not stop.is_set():accepted=bool(callback(token,result))
                            for e,_ in calls:
                                await ws.send(json.dumps({'type':'conversation.item.create','item':{
                                    'type':'function_call_output','call_id':e['call_id'],
                                    'output':json.dumps({'accepted':accepted,'result':result})}}))
                            break
                    else:result='SUPERSEDED'
        except asyncio.TimeoutError:result='TIMEOUT'
        except Exception as e:error=type(e).__name__
        finally:
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError,Exception):await sender
            self.status=result.lower() if accepted else result.lower()+'; blink remains available'
            log('grok_calls.jsonl',call_id=qid,candidate=label,result=result,accepted=accepted,
                api_attempted=attempted,error=error,latency_ms=round((time.perf_counter()-started)*1000,1),
                decision_latency_ms=None if speech_end is None else round((time.perf_counter()-speech_end)*1000,1))
