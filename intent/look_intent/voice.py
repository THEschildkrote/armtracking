"""Validated Grok tools and local-to-Chalk feedback; no networking dependencies."""
import json
import math
from dataclasses import asdict
from .feedback import Correction
from .memory import Workspace


def tool(name, description, properties=None, required=None):
    return dict(type='function',name=name,description=description,parameters=dict(
        type='object',properties=properties or {},required=required or [],additionalProperties=False))


TOOLS = [
    tool('select','Choose a candidate ID. correction=true only for explicit user corrections.',
         dict(object_id=dict(type='string'),correction=dict(type='boolean')),['object_id']),
    tool('shift','Correct selection left/right/closer/farther in table coordinates; never jog motors.',
         dict(direction=dict(type='string',enum=['left','right','closer','farther'])),['direction']),
    tool('cancel','Stop the task and cancel selection.'),
    tool('release','Request release of the held object at a controller-approved location.'),
    tool('put_back','Return held object to its locally validated remembered origin.',
         dict(object_id=dict(type='string')),['object_id']),
    tool('repeat_last','Pick the previous object again if currently available.'),
]
SCHEMAS = {t['name']:t['parameters'] for t in TOOLS}


class VoiceBridge:
    def __init__(self, shell, workspace=Workspace()):
        self.shell,self.workspace = shell,workspace
        self.results = {}

    def context(self,t):
        with self.shell.lock:
            q=self.shell.pending_voice
            if not q or t>=q['expires_at']:
                return None
            return dict(**q,objects=[asdict(o) for o in self.shell.objects],held_id=self.shell.held_id)

    def _validate(self,name,args):
        if name not in SCHEMAS or not isinstance(args,dict):
            raise ValueError('Unknown tool or invalid arguments')
        schema=SCHEMAS[name]
        if set(args)-set(schema['properties']) or set(schema['required'])-set(args):
            raise ValueError('Unexpected or missing arguments')
        for key,value in args.items():
            spec=schema['properties'][key]
            if spec['type']=='string' and (not isinstance(value,str) or not value or len(value)>128):
                raise ValueError('Expected a nonempty short string')
            if spec['type']=='boolean' and not isinstance(value,bool):
                raise ValueError('Expected boolean')
            if 'enum' in spec and value not in spec['enum']:
                raise ValueError('Unsupported direction')

    def execute(self,name,args,call_id,question_id,t):
        key=(question_id,call_id)
        with self.shell.lock:
            if key in self.results:
                return self.results[key]
            try:
                self._validate(name,args)
                q=self.shell.pending_voice
                if not q or q['question_id']!=question_id or t>=q['expires_at'] or q['scene_revision']!=self.shell.scene_revision:
                    raise ValueError('Voice question expired; ask again')
                result=self._dispatch(name,args,q,call_id,t)
            except (ValueError,TypeError) as exc:
                result=dict(ok=False,reason=str(exc))
            except Exception:
                self.shell.cancel_task(t)
                result=dict(ok=False,reason='Controller error; task stopped')
            self.shell.emit('voice_tool',t,name=name,call_id=call_id,question_id=question_id,result=result)
            self.results[key]=result
            if len(self.results)>512:
                self.results.pop(next(iter(self.results)))
            return result

    def _dispatch(self,name,args,q,call_id,t):
        s=self.shell
        if name=='cancel':
            s.cancel_task(t)
            return dict(ok=True,action='cancel')
        if name in ('release','put_back'):
            obj_id=args.get('object_id',s.held_id)
            if not obj_id or obj_id!=s.held_id or s.active_task:
                raise ValueError('Wait for delivery of the held object first')
            point=None
            if name=='put_back':
                point,reason=s.memory.put_back_target(obj_id,s.objects,self.workspace)
                if point is None:
                    raise ValueError(reason)
            s.active_task=f"voice-{q['question_id']}-{call_id}"
            s.emit('voice_select',t,object_id=obj_id,task_id=s.active_task,operation=name)
            if name=='release':
                s.arm.release(obj_id)
            else:
                s.arm.put_back(obj_id,*point)
            s.revision+=1
            s.close_voice(t,'accepted')
            return dict(ok=True,action=name,object_id=obj_id,target=point)
        candidates=[o for o in s.objects if o.id in q['candidates'] and o.id!=s.held_id]
        if name=='repeat_last':
            last=s.memory.last_task()
            if not last:
                raise ValueError('There is no previous task')
            object_id,correction=last.object_id,False
        elif name=='shift':
            base=next((o for o in s.objects if o.id==q['suggested_id']),None)
            if not base:
                raise ValueError('No suggested object to shift from; name an object')
            direction=args['direction']
            compatible=[o for o in candidates if
                (direction=='left' and o.x<base.x) or (direction=='right' and o.x>base.x) or
                (direction=='closer' and o.y<base.y) or (direction=='farther' and o.y>base.y)]
            if not compatible:
                raise ValueError('No candidate in that direction')
            distance=lambda o:math.hypot(o.x-base.x,o.y-base.y)
            compatible.sort(key=distance)
            if len(compatible)>1 and math.isclose(distance(compatible[0]),distance(compatible[1])):
                raise ValueError('Two candidates fit; name the object')
            object_id,correction=compatible[0].id,True
        else:
            object_id,correction=args['object_id'],args.get('correction',False)
        target=next((o for o in candidates if o.id==object_id),None)
        if not target:
            raise ValueError('Object is not an available candidate')
        if s.active_task or s.held_id:
            raise ValueError('Finish or cancel the active task first')
        wrong=next((o for o in s.objects if o.id==q['suggested_id']),None)
        if correction and (wrong is None or wrong.id==target.id):
            raise ValueError('No different suggested object to correct')
        if correction:
            history=s.memory.history_classes()
            c=Correction(f"{q['question_id']}:{call_id}",t,history[-1] if history else None,
                         wrong.id,wrong.cls,target.id,target.cls,q['query_id'])
            if s.feedback.record(c):
                s.emit('voice_correction',t,**{k:v for k,v in asdict(c).items() if k!='t'},question_id=q['question_id'])
        s.confirm(target.id,t,voice=True)
        s.close_voice(t,'accepted')
        return dict(ok=True,action='select',object_id=target.id,correction=correction)

    def handle_event(self,event,question_id,t):
        if event.get('type')!='response.function_call_arguments.done':
            return None
        call_id=event.get('call_id')
        if not isinstance(call_id,str) or not call_id:
            raise ValueError('Missing call_id')
        try:
            args=json.loads(event.get('arguments','{}'))
        except (TypeError,json.JSONDecodeError):
            args=None
        result=self.execute(event.get('name'),args,call_id,question_id,t)
        return dict(type='conversation.item.create',item=dict(type='function_call_output',
                    call_id=call_id,output=json.dumps(result)))
