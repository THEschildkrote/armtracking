"""Hardware-free adapter: python -m look_intent.shell --out /tmp/session.jsonl."""
import argparse
import json
import threading
from dataclasses import asdict

from .client import IntentService
from .memory import TaskMemory
from .scoring import Obj, Params, current_dwell


class MockArm:
    def __init__(self):
        self.hover = None
        self.grasps = []

    def request_prehover(self, object_id):
        self.hover = object_id

    def cancel_prehover(self):
        self.hover = None

    def grasp(self, object_id):
        self.grasps.append(object_id)


class Shell:
    """Arm adapter requires request_prehover(id), cancel_prehover(), grasp(id).

    A real controller must independently enforce calibration, authorization,
    reach, collisions and speed. This mock does not validate physical safety.
    tick() runs in a separate worker; all input timestamps share its clock.
    """
    def __init__(self, arm, service, log):
        self.arm, self.service, self.log = arm, service, log
        self.memory = TaskMemory()
        self.objects, self.gaze = [], []
        self.revision = 0
        self.fired = None
        self.lock = threading.RLock()

    def emit(self, kind, t, **fields):
        self.log(dict(type=kind, t=t, **fields))

    def cancel(self, t):
        self.arm.cancel_prehover()
        self.emit('prehover_cancel', t)

    def scene(self, objects, t):
        with self.lock:
            if self.objects != list(objects):
                self.cancel(t)
                self.objects, self.gaze = list(objects), []
                self.fired = None
                self.revision += 1
                self.emit('scene', t, objects=[asdict(o) for o in objects])

    def sample(self, t, x, y, valid=True):
        with self.lock:
            self.emit('gaze', t, x=x, y=y, valid=valid)
            self.revision += 1
            if not valid:
                self.gaze = []
                self.fired = None
                self.cancel(t)
                return
            self.gaze.append((t, x, y))
            self.gaze = [g for g in self.gaze if t-g[0] <= 1.5]
            candidates = sorted(self.objects, key=lambda o: (o.x-x)**2+(o.y-y)**2)
            target = candidates[0] if candidates else None
            d = lambda o: ((o.x-x)**2+(o.y-y)**2)**0.5
            ambiguous = len(candidates)>1 and d(candidates[1]) <= 1.5*d(target)
            if target is None or d(target)>40 or ambiguous:
                self.fired = None
                self.cancel(t)
                return
            if self.fired != target.id:
                self.fired = None
            if self.fired is None and current_dwell(self.gaze, target, t, Params(radius_mm=40)) >= 0.8:
                self.confirm(target.id, t)

    def confirm(self, object_id, t, voice=False):
        with self.lock:
            obj = next(o for o in self.objects if o.id == object_id)
            if not voice and current_dwell(self.gaze, obj, t, Params(radius_mm=40)) < 0.8:
                raise ValueError('Dwell incomplete')
            self.cancel(t)
            self.revision += 1
            self.emit('voice_select' if voice else 'fixation_fired', t, object_id=object_id)
            self.arm.grasp(object_id)
            self.emit('arm_descend', t, object_id=object_id)
            self.fired = object_id

    def grasp_ok(self, object_id, t):
        with self.lock:
            obj = next(o for o in self.objects if o.id == object_id)
            self.memory.record_pick(t, obj.id, obj.cls, obj.x, obj.y)
            self.emit('grasp_ok', t, object_id=obj.id, cls=obj.cls, x=obj.x, y=obj.y)

    def tick(self, t, clock=None):
        with self.lock:
            objects, gaze, rev = list(self.objects), list(self.gaze), self.revision
            history = self.memory.history_classes()
        result = self.service.predict(objects, gaze, history, t)
        now = clock() if clock else t
        with self.lock:
            if rev != self.revision:
                return result  # discard predictions computed against changed input
            fresh = bool(gaze) and now-gaze[-1][0] <= Params().max_gap_s
            if result.prehover and fresh and self.fired is None and result.object_id in {o.id for o in objects}:
                self.arm.request_prehover(result.object_id)
                self.emit('prehover_start', now, object_id=result.object_id)
            else:
                self.cancel(now)
        return result

    def run(self, stop, clock):
        try:
            while not stop.is_set():
                self.tick(clock(), clock)
                stop.wait(0.1)
        finally:
            with self.lock:
                self.cancel(clock())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    with open(args.out, 'w') as stream:
        def log(event):
            stream.write(json.dumps(event)+'\n')
            stream.flush()
        service = IntentService('synthetic', use_chalk=False, log=log)
        shell = Shell(MockArm(), service, log)
        shell.emit('session', 0, synthetic=True)
        shell.scene([Obj('c', 'cup', 0, 300), Obj('s', 'spoon', 150, 300)], 0)
        for i in range(31):
            t = i/30
            shell.sample(t, 0, 300)
            if i%3 == 0:
                shell.tick(t)
        shell.grasp_ok('c', 1.01)
        print('SYNTHETIC mock grasps:', shell.arm.grasps)


if __name__ == '__main__':
    main()
