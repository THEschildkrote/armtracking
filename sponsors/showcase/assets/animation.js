// Render the same arm-animation component used by the original Look website.
import {i as interop,n as react,t as runtime} from './reference/jsx-runtime-DyZJNswf.js';
import {createRoot as renderer} from './reference/render-runtime.js';
import RobotScene from './reference/RobotScene-BtfShsky.js';
const React=interop(react(),1),{jsx,jsxs}=runtime();
function Animation(){
 const [moment,setMoment]=React.useState('medicine');
 const [phase,setPhase]=React.useState('idle');
 React.useEffect(()=>{const change=e=>{if(['medicine','mug','water','phone'].includes(e.detail)){setMoment(e.detail);setPhase('idle')}};window.addEventListener('look:object',change);return()=>window.removeEventListener('look:object',change)},[]);
 return jsxs('div',{children:[jsx(RobotScene,{moment,phase,onComplete:()=>setPhase('done')}),jsxs('div',{className:'animation-controls',children:[jsx('span',{role:'status',children:phase==='reaching'?'Moving the object…':phase==='done'?'Delivery complete':'Original arm preview'}),jsx('button',{type:'button',onClick:()=>setPhase(phase==='reaching'?'idle':'reaching'),children:phase==='reaching'?'Stop preview':phase==='done'?'Replay animation':'Play animation'})]})]});
}
const mount=document.getElementById('original-arm');
if(mount)renderer.createRoot(mount).render(jsx(Animation,{}));
