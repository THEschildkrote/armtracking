"""Serialized controller shell; all positions in table mm, time monotonic seconds."""
import argparse
import json
import math
import threading
import time
import uuid
from dataclasses import asdict
from .client import IntentService
from .feedback import FeedbackMemory
from .memory import TaskMemory
from .scoring import Obj, Params, current_dwell


class MockArm:
    """Records dispatch only; real adapters independently enforce physical safety."""
    def __init__(self):
        self.hover = None
        self.grasps, self.returns = [], []
        self.releases = self.stops = 0

    def request_prehover(self, object_id):
        self.hover = object_id

    def cancel_prehover(self):
        self.hover = None

    def grasp(self, object_id):
        self.grasps.append(object_id)

    def stop(self):
        self.stops += 1
        self.hover = None

    def release(self, object_id):
        self.releases += 1

    def put_back(self, object_id, x, y):
        self.returns.append((object_id, x, y))


class Shell:
    def __init__(self, arm, service, log, memory=None, feedback=None):
        self.arm, self.service, self.log = arm, service, log
        self.memory = memory or TaskMemory()
        self.feedback = feedback or FeedbackMemory(service.user_id)
        self.objects, self.gaze = [], []
        self.revision = self.scene_revision = 0
        self.fired = self.hover = self.pending_voice = None
        self.active_task = self.held_id = self.last_prediction = None
        self.status = {}
        self.pick_origin = None
        self.lock = threading.RLock()

    def emit(self, kind, t, **fields):
        self.log(dict(type=kind, t=t, user_id=self.service.user_id, **fields))

    def cancel(self, t):
        self.arm.cancel_prehover()
        if self.hover:
            self.emit('prehover_cancel', t, object_id=self.hover)
        self.hover = None

    def close_voice(self, t, reason):
        if self.pending_voice:
            self.emit('voice_closed', t, question_id=self.pending_voice['question_id'], reason=reason)
        self.pending_voice = None

    def cancel_task(self, t):
        with self.lock:
            self.arm.stop()
            self.cancel(t)
            self.close_voice(t, 'cancel')
            self.emit('task_cancel', t, task_id=self.active_task)
            self.active_task = self.fired = None
            self.gaze = []
            self.revision += 1

    def scene(self, objects, t):
        with self.lock:
            objects = list(objects)
            if len({o.id for o in objects})!=len(objects) or any(
                    not math.isfinite(v) for o in objects for v in (o.x, o.y)):
                raise ValueError('Scene requires unique IDs and finite positions')
            # Detector jitter must not reset dwell or invalidate every voice turn.
            old = {o.id:o for o in self.objects}
            stable = len(old)==len(objects) and all(o.id in old and old[o.id].cls==o.cls
                and math.hypot(old[o.id].x-o.x, old[o.id].y-o.y)<=10 for o in objects)
            if not stable:
                self.cancel(t)
                self.close_voice(t, 'scene_changed')
                self.objects, self.gaze = objects, []
                self.fired = None
                self.revision += 1
                self.scene_revision += 1
                self.emit('scene', t, objects=[asdict(o) for o in objects])

    def request_voice(self, t, candidates=None, reason='ambiguous', suggested_id=None):
        with self.lock:
            if self.pending_voice:
                return dict(self.pending_voice)
            ids = candidates if candidates is not None else [o.id for o in self.objects]
            if (not ids and not self.held_id) or any(i not in {o.id for o in self.objects} for i in ids):
                raise ValueError('Candidates must exist in current scene')
            self.cancel(t)
            self.revision += 1
            self.pending_voice = dict(question_id=uuid.uuid4().hex, opened_at=t, expires_at=t+15,
                scene_revision=self.scene_revision, candidates=list(ids), suggested_id=suggested_id,
                query_id=self.service.last_query.get('query_id'), reason=reason)
            self.emit('voice_requested', t, **self.pending_voice)
            return dict(self.pending_voice)

    def sample(self, t, x, y, valid=True):
        with self.lock:
            if not isinstance(t,(int,float)) or not math.isfinite(t):
                raise ValueError('Timestamp must be finite')
            valid = bool(valid and all(isinstance(v, (int,float)) and math.isfinite(v) for v in (t,x,y)))
            if self.gaze and t<self.gaze[-1][0]:
                raise ValueError('Gaze timestamps must increase')
            self.emit('gaze', t, x=x if valid else None, y=y if valid else None, valid=valid)
            if not valid:
                self.gaze = []
                self.fired = None
                self.revision += 1
                self.cancel(t)
                return
            self.gaze.append((t,x,y))
            self.gaze = [g for g in self.gaze if t-g[0]<=1.5]
            if self.pending_voice:
                if t>=self.pending_voice['expires_at']:
                    self.close_voice(t, 'timeout')
                else:
                    return
            if self.active_task or self.held_id:
                return
            distance = lambda o: math.hypot(o.x-x, o.y-y)
            candidates = sorted(self.objects, key=distance)
            target = candidates[0] if candidates else None
            if not target or distance(target)>60:
                self.fired = None
                self.revision += 1
                self.cancel(t)
                return
            if len(candidates)>1 and distance(candidates[1])<=1.5*distance(target):
                self.request_voice(t, [o.id for o in candidates if distance(o)<=60],
                                   suggested_id=self.last_prediction)
                return
            if self.hover and self.hover!=target.id:
                self.cancel(t)
                self.revision += 1
            if self.fired!=target.id:
                self.fired = None
            if distance(target)<=40 and self.fired is None and current_dwell(
                    self.gaze, target, t, Params(radius_mm=40))>=0.8:
                self.confirm(target.id, t)

    def confirm(self, object_id, t, voice=False):
        with self.lock:
            obj = next((o for o in self.objects if o.id==object_id), None)
            if not obj:
                raise ValueError('Target absent from current scene')
            if self.active_task or self.held_id:
                raise ValueError('Finish or cancel the active task first')
            if not voice and current_dwell(self.gaze,obj,t,Params(radius_mm=40))<0.8:
                raise ValueError('Dwell incomplete')
            self.cancel(t)
            self.revision += 1
            self.active_task = uuid.uuid4().hex
            self.emit('voice_select' if voice else 'fixation_fired',t,object_id=object_id,
                      task_id=self.active_task,operation='pick')
            self.pick_origin = obj
            self.arm.grasp(object_id)
            self.emit('arm_descend',t,object_id=object_id,task_id=self.active_task)
            self.fired = object_id

    def grasp_ok(self, object_id, t):
        with self.lock:
            if not self.active_task or object_id!=self.fired or self.held_id:
                raise ValueError('No matching active pick')
            obj = self.pick_origin
            self.memory.record_pick(t,obj.id,obj.cls,obj.x,obj.y)
            self.held_id = object_id
            self.emit('grasp_ok',t,object_id=obj.id,cls=obj.cls,x=obj.x,y=obj.y,task_id=self.active_task)

    def delivered(self, object_id, t):
        with self.lock:
            if object_id!=self.held_id:
                raise ValueError('Object is not held')
            self.memory.record_delivered(object_id)
            self.emit('delivered',t,object_id=object_id,task_id=self.active_task)
            self.active_task = None

    def released(self, object_id, t):
        with self.lock:
            if object_id!=self.held_id:
                raise ValueError('Object is not held')
            self.emit('released',t,object_id=object_id,task_id=self.active_task)
            self.active_task = self.held_id = self.fired = None
            self.gaze = []
            self.revision += 1

    def tick(self, t, clock=None):
        with self.lock:
            objects,gaze,rev = list(self.objects),list(self.gaze),self.revision
            history,corrections = self.memory.history_classes(),self.feedback.snapshot()
        result = self.service.predict(objects,gaze,history,t,corrections=corrections)
        now = clock() if clock else t
        with self.lock:
            self.status = dict(**asdict(result),**self.service.last_query,voice_pending=bool(self.pending_voice))
            if rev!=self.revision:
                return result
            self.last_prediction = result.object_id
            if self.pending_voice and now>=self.pending_voice['expires_at']:
                self.close_voice(now,'timeout')
            fresh = bool(self.gaze) and 0<=now-self.gaze[-1][0]<=Params().max_gap_s
            eligible = (result.prehover and fresh and not self.pending_voice and not self.active_task
                        and not self.held_id and result.object_id in {o.id for o in objects})
            if eligible:
                target = next(o for o in self.objects if o.id==result.object_id)
                eligible = math.hypot(target.x-self.gaze[-1][1],target.y-self.gaze[-1][2])<=60
            if eligible:
                if self.hover!=result.object_id:
                    self.arm.request_prehover(result.object_id)
                    self.hover = result.object_id
                    self.emit('prehover_start',now,object_id=result.object_id,
                              query_id=self.service.last_query.get('query_id'))
            else:
                self.cancel(now)
        return result

    def run(self, stop, clock=time.monotonic):
        try:
            while not stop.is_set():
                started = clock()
                self.tick(started,clock)
                stop.wait(max(0,0.1-(clock()-started)))
        except Exception:
            self.cancel_task(clock())
            stop.set()
            raise
        finally:
            with self.lock:
                self.cancel(clock())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    with open(args.out,'w') as stream:
        def log(event):
            stream.write(json.dumps(event)+'\n')
        service=IntentService('synthetic',use_chalk=False,log=log)
        shell=Shell(MockArm(),service,log)
        shell.emit('session',0,synthetic=True)
        shell.scene([Obj('c','cup',0,300),Obj('s','spoon',150,300)],0)
        for i in range(31):
            shell.sample(i/30,0,300)
            if i%3==0:
                shell.tick(i/30)
        shell.grasp_ok('c',1.01)
        service.close()
        print('SYNTHETIC mock grasps:',shell.arm.grasps)


if __name__=='__main__':
    main()
