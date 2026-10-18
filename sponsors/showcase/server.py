"""Run: python -m sponsors.showcase.server --config sponsors/config.local.json."""
import argparse, asyncio, base64, json, ssl, threading, time, uuid, wave, subprocess, sys
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from sponsors.config import Config, ROOT
from sponsors.chalk_client import RoutineClient, rank
from sponsors.grok import api_key, TOOL, URL, parse_result
from sponsors.logging import log
HERE=Path(__file__).parent
LOCK=threading.Lock()
STATE={'busy':False,'events':[],'rows':[],'hour':9,'selected':None,'arm':'idle','grok':None,'cloud':None,'factory':None,'error':None}
def emit(kind,message,**data):
    STATE['events'].append({'time':datetime.now(timezone.utc).isoformat(),'kind':kind,'message':message,**data})
    STATE['events']=STATE['events'][-40:]
    log('showcase.jsonl',kind=kind,message=message,**data)

def evidence():
    p=ROOT/'reports/suggested_priors.json'
    if p.exists():
        d=json.loads(p.read_text());STATE['factory']={'historical':True,'generated_at':d['generated_at'],'session_id':d.get('provenance',{}).get('session_id'),'summary':(ROOT/'reports/routine_summary.md').read_text()}

async def voice(label, pcm=None):
    import certifi, websockets
    key=api_key(CONFIG)
    if not key:raise RuntimeError('Configure XAI_API_KEY locally')
    started=time.perf_counter();decision=None;calls=[];qid=uuid.uuid4().hex
    source='browser microphone' if pcm is not None else 'generated speech fixture'
    try:
        async with websockets.connect(URL,ssl=ssl.create_default_context(cafile=certifi.where()),additional_headers={'Authorization':'Bearer '+key},open_timeout=2,close_timeout=.2) as ws:
            await ws.send(json.dumps({'type':'session.update','session':{'voice':'eve','turn_detection':None,'tools':[TOOL],'instructions':'Classify the user audio about '+json.dumps(label)+'. Call confirm_candidate exactly once. Yes/grab it means CONFIRM; no/cancel means CANCEL; otherwise UNCLEAR. Do not speak.','audio':{'input':{'format':{'type':'audio/pcm','rate':24000}},'output':{'format':{'type':'audio/pcm','rate':24000}}}}}))
            async def ready():
                while True:
                    e=json.loads(await ws.recv())
                    if e.get('type')=='error':raise RuntimeError('xAI session rejected')
                    if e.get('type')=='session.updated':return
            await asyncio.wait_for(ready(),2)
            if pcm is None:
                with wave.open(str(HERE/'confirm.wav'),'rb') as w:pcm=w.readframes(w.getnframes())
            for i in range(0,len(pcm),4800):await ws.send(json.dumps({'type':'input_audio_buffer.append','audio':base64.b64encode(pcm[i:i+4800]).decode()}))
            await ws.send(json.dumps({'type':'input_audio_buffer.commit'}));decision=time.perf_counter()
            await ws.send(json.dumps({'type':'response.create'}))
            async def response():
                while True:
                    e=json.loads(await ws.recv())
                    if e.get('type')=='error':raise RuntimeError('xAI response rejected')
                    if e.get('type')=='response.function_call_arguments.done':calls.append(parse_result(e))
                    if e.get('type')=='response.done':
                        if e.get('response',{}).get('status')!='completed' or len(calls)!=1:raise RuntimeError('No single completed tool call')
                        return calls[0]
            result=await asyncio.wait_for(response(),CONFIG.voice_timeout_s)
            out={'result':result,'decision_latency_ms':round((time.perf_counter()-decision)*1000),'latency_ms':round((time.perf_counter()-started)*1000),'source':source,'call_id':qid}
            log('grok_calls.jsonl',**out,api_attempted=True,accepted=False,test='showcase_virtual_arm')
            return out
    except Exception:
        log('grok_calls.jsonl',result='ERROR',api_attempted=True,accepted=False,test='showcase_virtual_arm',call_id=qid,latency_ms=round((time.perf_counter()-started)*1000))
        raise

def action(name,body):
    if name=='rank':
        hour=int(body.get('hour',9))
        if hour not in (9,15):raise ValueError('Choose 09:00 or 15:00')
        STATE.update(hour=hour,selected=None,arm='idle',grok=None)
        emit('chalk','Querying deployed routine graph',hour=hour)
        CLIENT.warmup(['mug','water_bottle','phone','medication'],wait=True)
        p=CLIENT.query(['mug','water_bottle','phone','medication'],hour=hour,strict=True)
        STATE['rows']=rank([('mug','mug',.5),('bottle','water_bottle',.5)],p,CONFIG.routine_weight)
        STATE['cloud']=dict(CLIENT.last)
        emit('chalk','Live prior returned; recommendation only',**CLIENT.last)
    elif name=='select':
        label=body.get('label')
        if label not in ('mug','water_bottle'):raise ValueError('Invalid candidate')
        STATE.update(selected=label,arm='awaiting confirmation',grok=None)
        emit('gaze','Simulated completed dwell selects '+label)
    elif name in ('voice','blink'):
        if not STATE['selected'] or STATE['arm']!='awaiting confirmation':raise ValueError('Select a candidate first')
        if name=='voice':
            if not CONFIG.voice_enabled:raise RuntimeError('Voice disabled in config')
            pcm=base64.b64decode(body['pcm'],validate=True) if body.get('pcm') else None
            emit('grok','Sending '+('microphone audio' if pcm else 'generated “Yes, grab it” speech')+' to xAI')
            STATE['grok']=asyncio.run(voice(STATE['selected'],pcm))
            result=STATE['grok']['result'];emit('grok','Tool returned '+result,**STATE['grok'])
        else:result='CONFIRM';emit('blink','Simulated blink confirmation')
        if result=='CONFIRM':
            STATE['arm']='grabbing';emit('arm','Virtual arm authorized — no hardware command')
            time.sleep(2);STATE['arm']='delivered';emit('arm','Virtual delivery complete; excluded from real grab history')
        elif result=='CANCEL':STATE['arm']='cancelled'
    elif name=='factory':
        import os
        if not os.environ.get('FACTORY_API_KEY'):raise RuntimeError('Factory credential not configured; previous report remains historical')
        from sponsors.factory_agent import run
        emit('factory','Real Droid routine analysis started')
        run(CONFIG);evidence();STATE['factory']['historical']=False
        emit('factory','Fresh Factory report validated; deployment still required',session_id=STATE['factory']['session_id'])
    elif name=='deploy':
        if not CONFIG.chalk_enabled:raise RuntimeError('Chalk disabled')
        import os
        env=dict(os.environ);env['PATH']=str(Path.home()/'.chalk/bin')+os.pathsep+env.get('PATH','')
        emit('chalk','Submitting validated routine data and Factory report')
        proc=subprocess.run([sys.executable,str(ROOT/'scripts/deploy_routine.py')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=75)
        if proc.returncode:raise RuntimeError('Chalk deployment failed')
        STATE['deployment_submitted']=True
        emit('chalk','Deployment submitted; serving rollout must be verified by a fresh query')
    else:raise ValueError('Unknown action')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,data,status=200,ctype='application/json'):
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if self.path=='/api/state':self.send(json.dumps(STATE).encode())
        elif self.path=='/':self.send((HERE/'index.html').read_bytes(),ctype='text/html')
        elif self.path=='/confirm.wav':self.send((HERE/'confirm.wav').read_bytes(),ctype='audio/wav')
        else:self.send(b'{}',404)
    def do_POST(self):
        # Same-origin browser only; bind loopback and never expose credentials to JS.
        if self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(PORT),'http://localhost:'+str(PORT)):
            self.send(b'{}',403);return
        if not LOCK.acquire(False):self.send(b'{"error":"busy"}',409);return
        try:
            n=int(self.headers.get('Content-Length',0))
            if n>1500000:raise ValueError('Audio too large')
            body=json.loads(self.rfile.read(n) or b'{}')
        except Exception:LOCK.release();self.send(b'{}',400);return
        STATE.update(busy=True,error=None)
        def work():
            try:action(self.path.rsplit('/',1)[-1],body)
            except Exception as e:
                # No provider response bodies/credentials in browser output.
                STATE['error']=type(e).__name__+': '+('Voice unavailable; blink remains available.' if self.path.endswith('voice') else 'Action failed; see local logs/status. No simulated success.')
                emit('error',STATE['error'])
            finally:STATE['busy']=False;LOCK.release()
        threading.Thread(target=work,daemon=True).start();self.send(b'{"started":true}',202)

def main():
    global CONFIG, CLIENT, PORT
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--port',type=int,default=8765);p.add_argument('--factory-key-file');a=p.parse_args()
    if a.factory_key_file:
        import os, re, shlex
        raw=Path(a.factory_key_file).expanduser().read_text().strip()
        match=re.search(r'(?m)^(?:export\s+)?FACTORY_API_KEY\s*=\s*(.+)$',raw)
        key=shlex.split(match.group(1),comments=True)[0] if match else raw
        if not key or '\n' in key:raise ValueError('Invalid Factory key file')
        os.environ['FACTORY_API_KEY']=key
    CONFIG=Config.load(a.config);CLIENT=RoutineClient(CONFIG);PORT=a.port;evidence()
    print(f'Look showcase: http://127.0.0.1:{PORT}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
if __name__=='__main__':main()
