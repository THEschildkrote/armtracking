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
CONTEXT_LOCK=threading.Lock()
LABELS=('mug','water_bottle','phone','medication')
STATE={'busy':False,'context_busy':False,'context_error':None,'events':[],'rows':[],'hour':9,'selected':None,'arm':'idle','animation_id':None,'completed_animation_id':None,'grok':None,'cloud':None,'factory':None,'evaluation':None,'error':None}
def emit(kind,message,**data):
    STATE['events'].append({'time':datetime.now(timezone.utc).isoformat(),'kind':kind,'message':message,**data})
    STATE['events']=STATE['events'][-40:]
    log('showcase.jsonl',kind=kind,message=message,**data)

def evidence():
    p=ROOT/'reports/suggested_priors.json'
    if p.exists():
        d=json.loads(p.read_text());STATE['factory']={'historical':True,'generated_at':d['generated_at'],'session_id':d.get('provenance',{}).get('session_id'),'summary':(ROOT/'reports/routine_summary.md').read_text()}
        highlights=[]
        for block,hours in [('Morning',range(6,12)),('Afternoon',range(12,18)),('Evening',range(18,24))]:
            totals={}
            for hour in hours:
                for label,probability in d.get('hour_priors',{}).get(str(hour),{}).items():
                    totals[label]=totals.get(label,0)+probability
            if totals:highlights.append({'time':block,'object':max(totals,key=totals.get)})
        STATE['factory']['highlights']=highlights
        history=ROOT/'logs/factory_runs.jsonl'
        if history.exists():
            for line in reversed(history.read_text().splitlines()):
                try:entry=json.loads(line)
                except ValueError:continue
                if entry.get('session_id')==STATE['factory']['session_id']:
                    STATE['factory']['elapsed_s']=round(entry.get('latency_ms',0)/1000,1);break
    # Present the most recent completed report without running an evaluation or
    # contacting a sponsor service when the showcase starts.
    reports=ROOT/'logs/evaluations'
    if reports.exists():
        for path in sorted(reports.glob('*/evaluation.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
            try:
                saved=json.loads(path.read_text())
                if (not isinstance(saved,dict) or saved.get('failed')
                    or not isinstance(saved.get('run_id'),str) or not saved['run_id']
                    or not isinstance(saved.get('text'),str)
                    or not isinstance(saved.get('report'),dict)
                    or not isinstance(saved['report'].get('results'),dict)
                    or not saved['report']['results']):continue
            except (OSError,ValueError,TypeError):continue
            STATE['evaluation']=saved;break

def start_context(body):
    """Fetch real routine context independently of the manual selection gate."""
    hour=body.get('hour',9)
    if type(hour) is not int or not 0<=hour<=23:raise ValueError('Choose an integer hour from 0 to 23')
    profile=body.get('profile','new-user' if CONFIG.population_enabled else CONFIG.user_id)
    if profile not in ('new-user','demo'):raise ValueError('Unknown demo profile')
    if not CONTEXT_LOCK.acquire(False):return False
    STATE.update(context_busy=True,context_error=None)
    def work():
        try:
            from dataclasses import replace
            emit('chalk','Refreshing routine context',hour=hour)
            CLIENT.config=replace(CONFIG,user_id=profile)
            CLIENT.warmup(list(LABELS),wait=True)
            priors=CLIENT.query(list(LABELS),hour=hour,strict=True)
            rows=rank([(label,label,.25) for label in LABELS],priors,CONFIG.routine_weight)
            cloud=dict(CLIENT.last)
            STATE.update(hour=hour,profile=profile,rows=rows,cloud=cloud)
            emit('chalk','Routine context returned',**cloud)
        except Exception:
            # Cloud failure must not interrupt an independent object selection.
            STATE['context_error']='Routine context unavailable; previous context kept.'
            emit('chalk',STATE['context_error'])
        finally:
            STATE['context_busy']=False;CONTEXT_LOCK.release()
    try:threading.Thread(target=work,daemon=True).start()
    except Exception:
        STATE['context_busy']=False;CONTEXT_LOCK.release();raise
    return True

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
        if STATE['arm']=='grabbing':raise ValueError('Wait for the current request to finish')
        hour=body.get('hour',9)
        if type(hour) is not int or not 0<=hour<=23:raise ValueError('Choose an integer hour from 0 to 23')
        from dataclasses import replace
        profile=body.get('profile','new-user' if CONFIG.population_enabled else CONFIG.user_id)
        if profile not in ('new-user','demo'):raise ValueError('Unknown demo profile')
        CLIENT.config=replace(CONFIG,user_id=profile)
        STATE.update(hour=hour,selected=None,arm='idle',animation_id=None,completed_animation_id=None,grok=None,profile=profile)
        emit('chalk','Querying deployed routine graph',hour=hour)
        CLIENT.warmup(list(LABELS),wait=True)
        p=CLIENT.query(list(LABELS),hour=hour,strict=True)
        STATE['rows']=rank([(label,label,.25) for label in LABELS],p,CONFIG.routine_weight)
        STATE['cloud']=dict(CLIENT.last)
        emit('chalk','Live prior returned; recommendation only',**CLIENT.last)
    elif name=='select':
        if STATE['arm']=='grabbing':raise ValueError('Wait for the current request to finish')
        label=body.get('label')
        if label not in LABELS:raise ValueError('Invalid candidate')
        STATE.update(selected=label,arm='awaiting confirmation',animation_id=None,completed_animation_id=None,grok=None)
        emit('selection','Selected '+label)
    elif name=='confirm':
        if not STATE['selected'] or STATE['arm']!='awaiting confirmation':raise ValueError('Select a candidate first')
        STATE.update(arm='grabbing',animation_id=uuid.uuid4().hex,completed_animation_id=None)
        emit('arm','Confirmed request; arm animation started',label=STATE['selected'],animation_id=STATE['animation_id'])
    elif name=='animation_complete':
        animation_id=body.get('animation_id')
        # A callback is only authoritative for its own active animation. Reloads,
        # duplicate browser callbacks and an older animation cannot finish a new one.
        if (not isinstance(animation_id,str) or not animation_id
            or animation_id!=STATE.get('animation_id') or STATE['arm']!='grabbing'
            or animation_id==STATE.get('completed_animation_id')):return
        STATE.update(arm='delivered',completed_animation_id=animation_id)
        emit('arm','Arm animation completed',label=STATE['selected'],animation_id=animation_id)
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
    elif name=='evaluate':
        from sponsors.evaluation import run as evaluate
        emit('eval','Custom evaluation started')
        STATE['evaluation']=evaluate()
        emit('eval','Evaluation completed',run_id=STATE['evaluation']['run_id'])
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
        elif self.path.split('?',1)[0].startswith('/assets/'):
            import mimetypes
            from urllib.parse import unquote
            assets=(HERE/'assets').resolve()
            path=(assets/unquote(self.path.split('?',1)[0][len('/assets/'):])).resolve()
            if path.is_relative_to(assets) and path.is_file() and path.suffix in ('.js','.css','.json','.md'):
                self.send(path.read_bytes(),ctype=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            else:self.send(b'{}',404)
        elif self.path=='/confirm.wav':self.send((HERE/'confirm.wav').read_bytes(),ctype='audio/wav')
        else:self.send(b'{}',404)
    def do_POST(self):
        # Same-origin browser only; bind loopback and never expose credentials to JS.
        if self.headers.get('Origin') not in (None,'http://127.0.0.1:'+str(PORT),'http://localhost:'+str(PORT)):
            self.send(b'{}',403);return
        # A slow provider request must never hold the control lock or make the
        # studio appear busy while selecting and confirming its 3D animation.
        if self.path=='/api/context':
            try:
                n=int(self.headers.get('Content-Length',0))
                if not 0<=n<=1500000:raise ValueError('Invalid request size')
                body=json.loads(self.rfile.read(n) or b'{}')
                if not isinstance(body,dict):raise ValueError('Request object required')
                started=start_context(body)
            except (ValueError,TypeError):self.send(b'{}',400);return
            self.send(b'{"started":true}' if started else b'{"error":"context busy"}',202 if started else 409);return
        if not LOCK.acquire(False):self.send(b'{"error":"busy"}',409);return
        try:
            n=int(self.headers.get('Content-Length',0))
            if not 0<=n<=1500000:raise ValueError('Invalid request size')
            body=json.loads(self.rfile.read(n) or b'{}')
            if not isinstance(body,dict):raise ValueError('Request object required')
        except Exception:LOCK.release();self.send(b'{}',400);return
        STATE.update(busy=True,error=None,action=self.path.rsplit('/',1)[-1],started_at=time.time())
        def work():
            try:action(self.path.rsplit('/',1)[-1],body)
            except Exception as e:
                if self.path.endswith('evaluate'):
                    STATE['evaluation']={'text':('Evaluation timed out after 60 seconds.' if isinstance(e,subprocess.TimeoutExpired) else 'Evaluation failed. Check configured session paths, log format and backend availability.'),'failed':True}
                # No provider response bodies/credentials in browser output.
                STATE['error']=('Factory timed out after 45 seconds; previous report kept.' if isinstance(e,subprocess.TimeoutExpired) and self.path.endswith('factory') else type(e).__name__+': '+('Voice unavailable; blink remains available.' if self.path.endswith('voice') else 'Action failed; see local logs/status. No simulated success.'))
                emit('error',STATE['error'])
            finally:STATE['busy']=False;LOCK.release()
        threading.Thread(target=work,daemon=True).start();self.send(b'{"started":true}',202)

def main():
    global CONFIG, CLIENT, PORT
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--port',type=int,default=8765);p.add_argument('--factory-key-file');a=p.parse_args()
    if a.factory_key_file:
        import os
        from sponsors.credentials import read_key
        key=read_key(a.factory_key_file,'FACTORY_API_KEY')
        if not key:raise ValueError('Factory key missing from private file')
        os.environ['FACTORY_API_KEY']=key
    CONFIG=Config.load(a.config);CLIENT=RoutineClient(CONFIG);PORT=a.port;evidence()
    print(f'Look showcase: http://127.0.0.1:{PORT}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
if __name__=='__main__':main()
