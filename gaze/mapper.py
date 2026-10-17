"""Gaze mapping: pupil position (eye-camera px) -> where you look in the front-camera image.

The front camera is on the glasses, so this mapping does not change when the
head moves. Table millimetres then come from the front camera's per-frame
marker homography (scene.markers), which is what makes the result head-free.

Model: affine u = c . [1, x, y] or second-order polynomial [1, x, y, xy, x^2, y^2]
per axis - whichever has the lower leave-one-out error. Pupil coords are
normalised first so the least-squares fit is well conditioned.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

CALIB_PATH = Path(__file__).with_name("calib.json")


def _features(xy: np.ndarray, quadratic: bool) -> np.ndarray:
    x, y = xy[:, 0], xy[:, 1]
    cols = [np.ones_like(x), x, y]
    if quadratic:
        cols += [x * y, x * x, y * y]
    return np.stack(cols, axis=1)


def _solve(pupil_n: np.ndarray, scene: np.ndarray, quadratic: bool) -> np.ndarray:
    coef, *_ = np.linalg.lstsq(_features(pupil_n, quadratic), scene, rcond=None)
    return coef  # (n_features, 2)


@dataclass
class GazeCalibration:
    coef: list[list[float]]  # (n_features, 2): columns map to scene u, v
    quadratic: bool
    centre: list[float]  # pupil normalisation
    scale: float
    pupil: list[list[float]] = field(default_factory=list)  # calibration points, eye px
    scene: list[list[float]] = field(default_factory=list)  # matching targets, front-camera px
    loo_rms_px: float = float("nan")  # leave-one-out error in front-camera pixels
    loo_worst_px: float = float("nan")
    scene_size: list[int] = field(default_factory=list)  # front-camera width, height at calibration
    created: str = ""

    def map(self, x: float, y: float) -> tuple[float, float]:
        p = (np.array([[x, y]]) - self.centre) / self.scale
        u, v = (_features(p, self.quadratic) @ np.asarray(self.coef))[0]
        return float(u), float(v)

    def save(self, path: Path = CALIB_PATH) -> None:
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path = CALIB_PATH) -> GazeCalibration:
        return cls(**json.loads(path.read_text()))


def fit(pupil: np.ndarray, scene: np.ndarray, scene_size: tuple[int, int] = (0, 0)) -> GazeCalibration:
    pupil, scene = np.asarray(pupil, float), np.asarray(scene, float)
    n = len(pupil)
    if n < 3:
        raise ValueError("need at least 3 calibration points")
    centre = pupil.mean(axis=0)
    scale = float(max(pupil.std(axis=0).max(), 1e-6))
    pn = (pupil - centre) / scale

    def loo(quadratic: bool) -> np.ndarray:
        """Leave-one-out: predict each point from a fit to the others."""
        out = []
        for i in range(n):
            keep = np.arange(n) != i
            if keep.sum() < (7 if quadratic else 4):  # need a spare point beyond the unknowns
                continue
            c = _solve(pn[keep], scene[keep], quadratic)
            out.append(float(np.linalg.norm((_features(pn[i : i + 1], quadratic) @ c)[0] - scene[i])))
        return np.array(out) if out else np.array([np.nan])

    # Keep whichever model predicts unseen points better. With a small pupil spread (screen-sized
    # targets) the quadratic over-fits noise and the affine wins; wider coverage favours the quadratic.
    errors = loo(False)
    quadratic = False
    if n >= 8:
        quad_errors = loo(True)
        if np.sqrt(np.nanmean(quad_errors**2)) < np.sqrt(np.nanmean(errors**2)):
            errors, quadratic = quad_errors, True
    coef = _solve(pn, scene, quadratic)

    return GazeCalibration(
        coef=coef.tolist(),
        quadratic=quadratic,
        centre=centre.tolist(),
        scale=scale,
        pupil=pupil.tolist(),
        scene=scene.tolist(),
        loo_rms_px=float(np.sqrt(np.nanmean(errors**2))),
        loo_worst_px=float(np.nanmax(errors)),
        scene_size=list(scene_size),
        created=time.strftime("%Y-%m-%d %H:%M:%S"),
    )


@dataclass
class GazePoint:
    t: float
    u: float  # front-camera px (nan when not valid)
    v: float
    x_mm: float  # table mm (nan when the table markers are not in view)
    y_mm: float
    valid: bool
