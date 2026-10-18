// Reuse the original site's 3D arm for both the home preview and live studio.
import {i as interop,n as react,t as runtime} from './reference/jsx-runtime-DyZJNswf.js';
import {createRoot as renderer} from './reference/render-runtime.js';
import RobotScene from './reference/RobotScene-BtfShsky.js';
const React=interop(react(),1),{jsx,jsxs}=runtime();
const moments=new Set(['medicine','mug','water','phone']);
const phases=new Set(['idle','selected','reaching','done']);
const emit=(name,detail)=>window.dispatchEvent(new CustomEvent(name,{detail}));
function Animation({studio=false}){
 const [moment,setMoment]=React.useState(studio?'mug':'medicine');
 const [phase,setPhase]=React.useState('idle');
 const [animationId,setAnimationId]=React.useState(null);
 const currentAnimationId=React.useRef(null);
 const currentMoment=React.useRef(moment);
 currentMoment.current=moment;
 React.useEffect(()=>{
  const changeObject=e=>{
   const next=typeof e.detail==='string'?e.detail:e.detail?.moment;
   if(moments.has(next)){
    currentAnimationId.current=null;
    setAnimationId(null);setMoment(next);setPhase('idle');
   }
  };
  const changePhase=e=>{
   const next=typeof e.detail==='string'?e.detail:e.detail?.phase;
   if(phases.has(next)){
    const nonce=typeof e.detail==='object'?e.detail?.animationId??null:null;
    currentAnimationId.current=nonce;
    setAnimationId(nonce);setPhase(next);
   }
  };
  const changePage=e=>{
   const page=typeof e.detail==='string'?e.detail:e.detail?.page;
   if(page&&page!==(studio?'studio':'home')){
    currentAnimationId.current=null;
    setAnimationId(null);setPhase('idle');
   }
  };
  const objectEvent=studio?'look:studio-object':'look:object';
  window.addEventListener(objectEvent,changeObject);
  window.addEventListener('look:page',changePage);
  if(studio)window.addEventListener('look:studio-phase',changePhase);
  emit(studio?'look:studio-ready':'look:animation-ready',{});
  return()=>{
   window.removeEventListener(objectEvent,changeObject);
   window.removeEventListener('look:page',changePage);
   if(studio)window.removeEventListener('look:studio-phase',changePhase);
  };
 },[studio]);
 const complete=()=>{
  if(studio&&currentAnimationId.current!==animationId)return;
  setPhase('done');
  if(studio)emit('look:studio-complete',{moment,animationId});
 };
 const select=()=>{
  setPhase('selected');
  emit('look:studio-select',{moment:currentMoment.current});
 };
 // A new request owns a fresh scene, so its completion keeps that request's ID.
 const scene=jsx(RobotScene,{moment,phase,onComplete:complete,interactive:studio,onSelect:studio?select:undefined},studio?`${moment}:${animationId??'idle'}`:moment);
 if(studio)return scene;
 return jsxs('div',{children:[scene,jsxs('div',{className:'animation-controls',children:[
  jsx('span',{role:'status',children:phase==='reaching'?'Moving the object…':phase==='done'?'Delivery complete':'Arm preview'}),
  jsx('button',{type:'button',onClick:()=>setPhase(phase==='reaching'?'idle':'reaching'),children:phase==='reaching'?'Stop preview':phase==='done'?'Replay animation':'Play animation'})
 ]})]});
}
for(const [id,studio] of [['original-arm',false],['studio-arm',true]]){
 const mount=document.getElementById(id);
 if(mount)renderer.createRoot(mount).render(jsx(Animation,{studio}));
}
