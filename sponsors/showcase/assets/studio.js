/* Studio presentation: one 3D selection/confirmation path; Chalk runs independently. */
let state={},studioMoment='mug',sceneReady=false,uiPending=false,hasChosenObject=false;
let initialized=false,refreshPending=false,lastScene='',lastCloudId='',lastEvents='',acknowledging=null;
let queuedContext=false,lastContextRequest='',lastContextAt=0;
const $=id=>document.getElementById(id);
const objectLabels={medicine:'Medicine',mug:'Mug',water:'Water bottle',phone:'Phone'};
const backendLabels={medicine:'medication',mug:'mug',water:'water_bottle',phone:'phone'};
const sceneMoments={medication:'medicine',mug:'mug',water_bottle:'water',phone:'phone'};
const dispatch=(name,detail)=>window.dispatchEvent(new CustomEvent(name,{detail}));
const grabbing=()=>state.arm==='grabbing';
function timeBucket(){const value=$('context-time').value;return value?Number(value.split(':')[0]):new Date().getHours();}
function timeLabel(hour){return new Intl.DateTimeFormat(undefined,{hour:'numeric'}).format(new Date(2026,0,1,hour));}
async function postAction(action,body={}){
 const response=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if(!response.ok)throw new Error(response.status===409?'The system is finishing the previous action.':'The action could not start.');
}
async function settle(check){
 const started=Date.now();
 do{await refresh();if(check(state))return;await new Promise(r=>setTimeout(r,120));}while(Date.now()-started<4000);
 throw new Error('The local controller has not acknowledged the action.');
}
function syncScene(force=false){
 if(!sceneReady||document.body.dataset.page!=='studio')return;
 const selected=state.selected===backendLabels[studioMoment];
 const phase=selected?({'awaiting confirmation':'selected',grabbing:'reaching',delivered:'done'})[state.arm]||'idle':'idle';
 const nonce=selected?state.animation_id||null:null;
 const key=studioMoment+'|'+phase+'|'+nonce;
 if(force||lastScene!==key){
  if(force||lastScene.split('|')[0]!==studioMoment)dispatch('look:studio-object',{moment:studioMoment});
  dispatch('look:studio-phase',{phase,animationId:nonce});lastScene=key;
 }
}
function updateControls(){
 const selected=state.selected===backendLabels[studioMoment]&&state.arm==='awaiting confirmation';
 document.querySelectorAll('[data-studio-object]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.studioObject===studioMoment));b.disabled=uiPending||grabbing();});
 $('confirm-reach').disabled=!selected||uiPending||grabbing();
 $('confirm-reach').textContent=grabbing()?'Reaching…':state.arm==='delivered'&&state.selected===backendLabels[studioMoment]?'Reach complete':'Confirm reach';
 if(grabbing()){$('studio-status').textContent='Moving '+objectLabels[studioMoment].toLowerCase();$('studio-hint').textContent='The 3D sequence is in progress.';}
 else if(state.arm==='delivered'&&state.selected===backendLabels[studioMoment]){$('studio-status').textContent='Placed within reach.';$('studio-hint').textContent='Choose another object to continue.';}
 else if(selected){$('studio-status').textContent=objectLabels[studioMoment]+' selected';$('studio-hint').textContent='Confirm to begin the reach.';}
 else{$('studio-status').textContent=objectLabels[studioMoment]+' in view';$('studio-hint').textContent='Select the object, then confirm the reach.';}
}
async function chooseObject(moment){
 if(!backendLabels[moment]||uiPending||grabbing())return;
 hasChosenObject=true;studioMoment=moment;lastScene='';syncScene(true);uiPending=true;updateControls();
 try{await postAction('select',{label:backendLabels[moment]});await settle(s=>!s.busy&&s.selected===backendLabels[moment]&&s.arm==='awaiting confirmation');$('error').textContent='';}
 catch(e){$('error').textContent=e.message;}
 finally{uiPending=false;updateControls();syncScene();}
}
async function confirmReach(){
 if(uiPending||grabbing()||state.selected!==backendLabels[studioMoment]||state.arm!=='awaiting confirmation')return;
 uiPending=true;updateControls();
 try{await postAction('confirm');await settle(s=>!s.busy&&s.arm==='grabbing'&&!!s.animation_id);$('error').textContent='';}
 catch(e){$('error').textContent=e.message;}
 finally{uiPending=false;updateControls();syncScene();}
}
async function completeReach(event){
 const {moment,animationId}=event.detail||{};
 if(!animationId||animationId!==state.animation_id||moment!==studioMoment||!grabbing()||acknowledging===animationId)return;
 acknowledging=animationId;
 try{await postAction('animation_complete',{animation_id:animationId});await settle(s=>!s.busy&&s.arm==='delivered'&&s.animation_id===animationId);}
 catch(e){$('error').textContent=e.message;}
 finally{acknowledging=null;updateControls();}
}
function updateContext(){
 const cloud=state.cloud;
 if(state.context_busy){$('context-signal').textContent='Refreshing time-of-day context…';$('context-dot').classList.remove('live');return;}
 if(state.context_error){$('context-signal').textContent='Context unavailable · the 3D demo remains available';$('context-dot').classList.remove('live');return;}
 if(!cloud||cloud.result!=='success'){$('context-signal').textContent='Routine context has not loaded yet';return;}
 const hour=Number.isInteger(cloud.hour_bucket)?cloud.hour_bucket:state.hour;
 $('context-signal').textContent='Chalk context active for '+timeLabel(hour)+' · '+Math.round(cloud.latency_ms)+' ms';
 $('context-dot').classList.add('live');
 $('context-receipt').textContent='Query '+cloud.query_id+'\nDeployment '+(cloud.cloud_evidence?.deployment_id||'unavailable');
 if(lastCloudId!==cloud.query_id){
  lastCloudId=cloud.query_id;
  if(!hasChosenObject&&!grabbing()&&state.arm==='idle'){
   const entries=Object.entries(cloud.priors||{}).filter(([key,p])=>sceneMoments[key]&&Number.isFinite(p));
   entries.sort((a,b)=>b[1]-a[1]);
   if(entries.length){studioMoment=sceneMoments[entries[0][0]];lastScene='';syncScene(true);}
  }
 }
}
async function requestContext(force=false){
 const key=String(timeBucket());
 if(state.context_busy){if(lastContextRequest!==key)queuedContext=true;return;}
 if(!force&&lastContextRequest===key&&Date.now()-lastContextAt<60000)return;
 lastContextRequest=key;lastContextAt=Date.now();queuedContext=false;
 try{
  const response=await fetch('/api/context',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hour:timeBucket(),profile:'new-user'})});
  if(response.status===409){queuedContext=true;return;}
  if(!response.ok)throw new Error('Context unavailable');
  await refresh();
 }catch{$('context-signal').textContent='Context unavailable · the 3D demo remains available';}
}
async function refresh(){
 if(refreshPending)return;
 refreshPending=true;
 try{
  const response=await fetch('/api/state');if(!response.ok)throw new Error('Disconnected');
  state=await response.json();
  if(!initialized){initialized=true;if(sceneMoments[state.selected]&&state.arm!=='idle'){studioMoment=sceneMoments[state.selected];hasChosenObject=true;}}
  $('activity').textContent=grabbing()?'Reaching':state.context_busy?'Context loading':'Ready';
  updateContext();updateControls();syncScene();
  const events=(state.events||[]).slice(-6).reverse(),key=JSON.stringify(events);
  if(lastEvents!==key){lastEvents=key;$('events').replaceChildren(...events.map(e=>{const row=document.createElement('div');row.className='event';const kind=document.createElement('b');kind.textContent=e.kind==='gaze'?'SELECTION':e.kind.toUpperCase();const message=document.createElement('span');message.textContent=e.message;row.append(kind,message);return row;}));}
  if(state.factory){$('factorybadge').textContent='Saved routine analysis';$('factory-summary').textContent=state.factory.highlights?.length?'Time-of-day patterns from the saved demo history.':'No saved pattern summary.';renderRoutineHighlights(state.factory.highlights||[]);$('report').textContent=state.factory.summary||'';}
  renderEvaluation(state.evaluation,false);$('evaluation').textContent=state.evaluation?.text||'No completed evaluation report has been saved.';
  if(queuedContext&&!state.context_busy&&document.body.dataset.page==='studio'){queuedContext=false;queueMicrotask(()=>requestContext(true));}
 }catch{$('activity').textContent='Local server disconnected';}
 finally{refreshPending=false;}
}
async function initializeStudio(){
 const now=new Date();$('context-time').value=String(now.getHours()).padStart(2,'0')+':'+String(now.getMinutes()).padStart(2,'0');
 document.querySelectorAll('[data-studio-object]').forEach(b=>b.addEventListener('click',()=>chooseObject(b.dataset.studioObject)));
 $('confirm-reach').addEventListener('click',confirmReach);
 $('context-time').addEventListener('change',()=>{hasChosenObject=false;requestContext(true);});
 window.addEventListener('look:studio-ready',()=>{sceneReady=true;syncScene(true);});
 window.addEventListener('look:studio-select',e=>chooseObject(e.detail?.moment));
 window.addEventListener('look:studio-complete',completeReach);
 window.addEventListener('look:page',e=>{if(e.detail?.page==='studio'){lastScene='';syncScene(true);requestContext();}});
 await refresh();if(document.body.dataset.page==='studio')requestContext();
 setInterval(refresh,900);
}
