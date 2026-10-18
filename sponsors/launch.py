"""Launch unchanged gaze.live with opt-in factory injection, scoped to this process."""
import argparse
import sys
from .config import Config

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config');args,core_args=parser.parse_known_args()
    config=Config.load(args.config)
    import gaze.intent_bridge as bridge
    from gaze import live
    if not (config.voice_enabled or config.chalk_enabled):
        sys.argv=['gaze.live',*core_args];live.main();return
    if '--intent' not in core_args:core_args.append('--intent')
    if '--blink-action' in core_args or '--arm' in core_args:
        parser.error('Sponsor launcher uses --intent; omit --arm/--blink-action')
    original=bridge.make_intent
    resources=[]
    def make_intent(arm_port='COM4',use_chalk=False,user_id='demo'):
        from arm.intent_arm import IntentArm
        from .bridge import SponsorShell
        from .chalk_client import RoutineClient,RoutineService
        log=bridge.JsonlLog()
        base=bridge.IntentService(config.user_id,use_chalk=use_chalk,log=log.write)
        service=RoutineService(base,RoutineClient(config)) if config.chalk_enabled else base
        box={}
        arm=IntentArm(config.arm_port,log=log.write,
            on_grasp_ok=lambda oid,t,verified:box['shell'].grasp_done(oid,t,verified),
            on_grasp_failed=lambda oid,t,reason:box['shell'].pick_failed(oid,t,reason))
        shell=SponsorShell(arm,service,log.write,config=config,memory_path=bridge.LOG_DIR/'task_memory.json')
        box['shell']=shell
        close_log=log.close
        def close_with_sponsors():
            with shell.lock:
                shell._invalidate_voice()
            close_log()
        log.close=close_with_sponsors
        import time
        shell.emit('session',time.monotonic(),synthetic=False,mode='blink_or_voice',
                   voice_enabled=config.voice_enabled,routine_chalk=config.chalk_enabled)
        shell.scene(bridge.load_scene(),time.monotonic());resources.append(shell)
        if config.chalk_enabled:service.client.warmup([o.cls for o in shell.objects])
        return shell,arm,log
    bridge.make_intent=make_intent
    # Separate debug window: no edits to core rendering or camera processing.
    original_imshow=live.cv2.imshow
    def show(window,image):
        original_imshow(window,image)
        if resources:
            import numpy as np
            panel=np.zeros((200,800,3),dtype=np.uint8)
            shell=resources[0]
            lines=['LOOK / live sponsor status',shell.message]
            if shell.voice:lines.append('Grok: '+shell.voice.status)
            for i,r in enumerate(getattr(shell.service,'rows',[])):
                p='fallback' if r['prior'] is None else f"{r['prior']:.3f}"
                lines.append(f"{i+1}. {r['label']}   gaze {r['gaze']:.2f}   Chalk prior {p}")
            for i,line in enumerate(lines[:7]):
                live.cv2.putText(panel,line,(12,26+i*24),live.cv2.FONT_HERSHEY_SIMPLEX,.55,(170,245,220),1)
            original_imshow('Look sponsors',panel)
    live.cv2.imshow=show
    try:
        sys.argv=['gaze.live',*core_args];live.main()
    finally:
        bridge.make_intent=original;live.cv2.imshow=original_imshow
        for shell in resources:shell._invalidate_voice()

if __name__=='__main__':main()
