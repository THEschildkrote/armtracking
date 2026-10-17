"""User-scoped explicit corrections, separate from physically successful picks."""
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Correction:
    event_id: str
    t: float
    previous_cls: str | None
    wrong_id: str
    wrong_cls: str
    correct_id: str
    correct_cls: str
    query_id: str | None = None


class FeedbackMemory:
    def __init__(self, user_id, path=None):
        self.user_id, self.path = user_id, Path(path) if path else None
        self.corrections = []
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            if data['user_id'] != user_id:
                raise ValueError('Feedback belongs to another user')
            self.corrections = [Correction(**d) for d in data['corrections']]

    def record(self, correction):
        if any(c.event_id == correction.event_id for c in self.corrections):
            return False
        self.corrections.append(correction)
        self.corrections = self.corrections[-200:]
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(dict(user_id=self.user_id, corrections=self.snapshot())))
            os.replace(tmp, self.path)
        return True

    def snapshot(self):
        return [asdict(c) for c in self.corrections]
