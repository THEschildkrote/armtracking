"""Runnable Grok→controller→correction memory→Chalk integration demo.

Default: synthetic, no credentials or hardware. --live: real Grok mic + mock arm;
scene/gaze/outcome events arrive as JSONL on stdin. --chalk enables online calls.
"""
import argparse
import asyncio
import json
import sys
import threading
import time
from pathlib import Path

from .client import IntentService
from .feedback import FeedbackMemory
from .grok_voice import GrokVoice
from .memory import TaskMemory
from .scoring import Obj, transition_prior
from .shell import MockArm, Shell
from .voice import VoiceBridge


class EventLog:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.stream = open(path, 'w')
        self.lock = threading.Lock()

    def write(self, event):
        with self.lock:
            self.stream.write(json.dumps(event, allow_nan=False)+'\n')
            self.stream.flush()

    def close(self):
        self.stream.close()


def synthetic(shell):
    shell.emit('session', 0, synthetic=True, history=['block'], corrections=[])
    shell.memory.record_pick(0, 'b', 'block', 150, 300)
    shell.scene([Obj('c', 'cup', -100, 300), Obj('s', 'spoon', 100, 300)], 0)
    for i in range(13):
        shell.sample(i/30, -100, 300)
        if i%3==0:
            shell.tick(i/30)
    before = transition_prior(['block'], ['cup','spoon'])
    question = shell.request_voice(0.5, suggested_id='c', reason='demo_correction')
    bridge = VoiceBridge(shell)
    result = bridge.handle_event(dict(type='response.function_call_arguments.done',
        call_id='synthetic-correction', name='select',
        arguments=json.dumps(dict(object_id='s', correction=True))), question['question_id'], 0.6)
    after = transition_prior(['block'], ['cup','spoon'], corrections=shell.feedback.snapshot())
    shell.tick(0.7)
    shell.grasp_ok('s', 0.8)
    shell.delivered('s', 1.0)
    print(json.dumps(dict(synthetic=True, tool_result=json.loads(result['item']['output']),
                         prior_before=before, prior_after=after,
                         mock_grasps=shell.arm.grasps, status=shell.status), indent=2))


async def live(shell):
    start = time.monotonic()
    clock = lambda: time.monotonic()-start
    stop = threading.Event()
    shell.emit('session', 0, synthetic=False, mock_arm=True,
               history=shell.memory.history_classes(), corrections=shell.feedback.snapshot())
    worker = threading.Thread(target=shell.run, args=(stop, clock), daemon=True)
    worker.start()

    def ingest():
        try:
            for line in sys.stdin:
                event = json.loads(line)
                kind, t = event['type'], clock()
                if kind=='scene':
                    shell.scene([Obj(**o) for o in event['objects']], t)
                elif kind=='gaze':
                    shell.sample(t, event.get('x', 0), event.get('y', 0), event.get('valid', True))
                elif kind=='question':
                    shell.request_voice(t, reason=event.get('reason', 'ambiguous'),
                                        suggested_id=event.get('suggested_id'))
                elif kind in ('grasp_ok', 'delivered', 'released'):
                    getattr(shell, kind)(event['object_id'], t)
                elif kind=='stop':
                    break
                else:
                    raise ValueError('Unsupported input event')
        except Exception as exc:
            print(f'Input stopped: {type(exc).__name__}', file=sys.stderr)
            shell.cancel_task(clock())
        finally:
            shell.cancel_task(clock())
            stop.set()
    threading.Thread(target=ingest, daemon=True).start()
    voice = GrokVoice(VoiceBridge(shell), clock=clock)
    last_status = -1
    try:
        while not stop.is_set():
            if shell.pending_voice:
                try:
                    await voice.answer_question()
                except Exception as exc:
                    print(f'Voice unavailable: {type(exc).__name__}', file=sys.stderr)
                    shell.cancel_task(clock())
            else:
                await asyncio.sleep(0.05)
            if int(clock())!=last_status:
                print(json.dumps(shell.status), file=sys.stderr)
                last_status = int(clock())
    finally:
        stop.set()
        shell.cancel_task(clock())
        await asyncio.to_thread(worker.join, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--user', default='demo')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--chalk', action='store_true')
    parser.add_argument('--memory-dir')
    args = parser.parse_args()
    folder = Path(args.memory_dir) if args.memory_dir else None
    if folder:
        folder.mkdir(parents=True, exist_ok=True)
    log = EventLog(args.out)
    service = IntentService(args.user, use_chalk=args.chalk, log=log.write)
    feedback = FeedbackMemory(args.user, folder/'feedback.json' if folder else None)
    memory = TaskMemory(str(folder/'picks.json') if folder else None)
    if not args.live and (folder or args.chalk):
        parser.error('Synthetic demo must not change persistent memories or call Chalk')
    shell = Shell(MockArm(), service, log.write, memory, feedback)
    try:
        if args.live:
            asyncio.run(live(shell))
        else:
            synthetic(shell)
    finally:
        service.close()
        log.close()


if __name__=='__main__':
    main()
