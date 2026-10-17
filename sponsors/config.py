from dataclasses import dataclass, fields
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Config:
    voice_enabled: bool = False
    chalk_enabled: bool = False
    factory_enabled: bool = False
    user_id: str = 'demo'
    timezone: str = 'America/New_York'
    routine_weight: float = 0.7
    agent_weight: float = 0.2
    chalk_timeout_s: float = 0.5
    chalk_refresh_s: float = 1.0
    voice_timeout_s: float = 2.0
    voice_listen_s: float = 10.0
    arm_port: str = 'COM4'

    @classmethod
    def load(cls, path):
        raw = json.loads(Path(path).read_text()) if path else {}
        if set(raw) - {f.name for f in fields(cls)}:
            raise ValueError('Unknown sponsor config fields')
        c = cls(**raw)
        for key in ('voice_enabled', 'chalk_enabled', 'factory_enabled'):
            if type(getattr(c, key)) is not bool: raise ValueError(key + ' must be boolean')
        for key in ('routine_weight','agent_weight','chalk_timeout_s','chalk_refresh_s','voice_timeout_s','voice_listen_s'):
            v=getattr(c,key)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0:
                raise ValueError('Invalid '+key)
        if not 0<=c.agent_weight<=1 or not 0<c.chalk_timeout_s<=2 or not 0<c.voice_timeout_s<=2 or c.chalk_refresh_s<0.1 or c.voice_listen_s<=0:
            raise ValueError('Invalid time budget or weight')
        from zoneinfo import ZoneInfo
        ZoneInfo(c.timezone)
        return c
