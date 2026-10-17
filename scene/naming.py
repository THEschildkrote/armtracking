"""Open-vocabulary object names with YOLOE: say what to look for in words ("granola bar").

The text model (mobileclip, ~570 MB) is only needed once per list of prompts: the prompt
embeddings are cached in models/ so later starts load just the small detector.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
WEIGHTS = MODELS / "yoloe-11s-seg.pt"


class Namer:
    def __init__(self, prompts: list[str], conf: float = 0.1):
        import torch
        from ultralytics import YOLOE

        self.prompts = list(prompts)
        self.conf = conf
        self.device = 0 if torch.cuda.is_available() else "cpu"
        cwd = os.getcwd()
        os.chdir(MODELS)  # ultralytics looks for (and would re-download) its helper files in the working folder
        try:
            self.model = YOLOE(str(WEIGHTS))
            cache = MODELS / f"text_pe_{hashlib.md5('|'.join(self.prompts).encode()).hexdigest()[:10]}.pt"
            if cache.exists():
                pe = torch.load(cache)
            else:
                pe = self.model.get_text_pe(self.prompts)
                torch.save(pe, cache)
            self.model.set_classes(self.prompts, pe)
        finally:
            os.chdir(cwd)

    def __call__(self, frame: np.ndarray) -> list[tuple[str, float, tuple[float, float, float, float]]]:
        """[(prompt, confidence, (x1, y1, x2, y2) pixels), ...]"""
        r = self.model.predict(frame, conf=self.conf, verbose=False, device=self.device)[0]
        return [(self.prompts[int(c)], float(k), tuple(b))
                for b, c, k in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist())]
