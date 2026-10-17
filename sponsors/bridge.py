"""Opt-in subclass; the protected gaze and arm files are never edited."""
import math
import time
import uuid
from gaze.intent_bridge import BlinkShell,CONFIRM_WINDOW_S,CONFIRM_DWELL_S,RADIUS_MM
from look_intent.scoring import current_dwell,Params
from .grok import VoiceConfirmation
from .logging import grab_event

class SponsorShell(BlinkShell):
    def __init__(self,*args,config,**kwargs):
        super().__init__(*args,**kwargs)
        self.config=config;self.voice=VoiceConfirmation(config) if config.voice_enabled else None
        self.voice_token=None;self.voice_attempted=None;self.voice_method=False
        self.message='Blink confirmation ready';self.next_debug=0.

    def _invalidate_voice(self):
        self.voice_token=None
        if self.voice:self.voice.cancel()

    def sample(self,t,x,y,valid=True):
        with self.lock:
            super().sample(t,x,y,valid)
            current=self.confirmable[0] if self.confirmable else None
            # Extra voice freshness checks do not alter the core's blink policy.
            if not valid or current!=self.dwelling or self.active_task or self.held_id:
                self._invalidate_voice()
                if not valid or self.dwelling!=current:self.voice_attempted=None
                return
            if self.voice_token and self.voice_token[0]!=current:self._invalidate_voice()
            if self.voice and current and self.voice_attempted!=current:
                token=(current,self.scene_revision,uuid.uuid4().hex)
                obj=next(o for o in self.objects if o.id==current)
                if self.voice.start(token,obj.cls,self.voice_result):
                    self.voice_token=token;self.voice_attempted=current
                    self.message=f'{obj.cls}: blink or say yes / no'

    def voice_result(self,token,result):
        with self.lock:
            now=time.monotonic()
            if (token!=self.voice_token or token[1]!=self.scene_revision or not self.confirmable
                or self.confirmable[0]!=token[0] or self.dwelling!=token[0]
                or now-self.confirmable[1]>CONFIRM_WINDOW_S or self.active_task or self.held_id
                or not self.gaze or now-self.gaze[-1][0]>.12):return False
            obj=next((o for o in self.objects if o.id==token[0]),None)
            if obj is None or current_dwell(self.gaze,obj,now,Params(radius_mm=RADIUS_MM))<CONFIRM_DWELL_S:return False
            if result=='CANCEL':
                self.cancel_all(now);self.message='Cancelled — look again';return True
            if result!='CONFIRM':self.message='Unclear — blink to confirm';return False
            # Same lock and same core gate as blink: the first accepted input wins.
            self.voice_method=True
            try:
                picked=self.blink_confirm(now,self.gaze[-1][1],self.gaze[-1][2])
            finally:self.voice_method=False
            return picked is not None

    def blink_confirm(self,t,x=None,y=None):
        with self.lock:
            picked=super().blink_confirm(t,x,y)
            if picked:
                self._invalidate_voice()
                obj=next(o for o in self.objects if o.id==picked)
                method='grok_voice' if self.voice_method else 'long_blink'
                grab_event(self.config.user_id,obj.cls,'confirmed',method)
                self.message=f'Grabbing {obj.cls} ({method})'
            return picked

    def emit(self,kind,t,**fields):
        if kind=='fixation_fired' and getattr(self,'voice_method',False):fields['method']='grok_voice'
        super().emit(kind,t,**fields)

    def cancel_all(self,t):
        with self.lock:
            oid=self.confirmable[0] if self.confirmable else None
            if oid:
                obj=next((o for o in self.objects if o.id==oid),None)
                if obj:grab_event(self.config.user_id,obj.cls,'cancelled','voice_or_blink')
            self._invalidate_voice()
            super().cancel_all(t)

    def scene(self,objects,t):
        with self.lock:
            rev=self.scene_revision
            super().scene(objects,t)
            if self.scene_revision!=rev:
                self._invalidate_voice();self.voice_attempted=None

    def tick(self,t,clock=None):
        result=super().tick(t,clock)
        if time.monotonic()>=self.next_debug:
            self.next_debug=time.monotonic()+1
            rows=getattr(self.service,'rows',[])
            if rows:
                print('CHALK ROUTINE | '+' | '.join(f"{r['label']}: gaze={r['gaze']:.2f} prior={r['prior'] if r['prior'] is not None else 'unavailable'} rank={i+1}" for i,r in enumerate(rows)),flush=True)
        return result

    def run(self,stop,clock=time.monotonic):
        try:super().run(stop,clock)
        finally:
            self._invalidate_voice()
            self.service.close()
