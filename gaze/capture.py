"""Read a camera in a background thread so two cameras at different rates don't block each other."""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
# Wi-Fi (MJPEG over HTTP) streams: tell FFmpeg not to buffer, so frames arrive with minimal lag.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "fflags;nobuffer|flags;low_delay")
import cv2  # noqa: E402
import numpy as np  # noqa: E402


def open_camera(source: str | int) -> cv2.VideoCapture:
    source = str(source)
    if source.isdigit():
        return cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
    return cv2.VideoCapture(source)


class CameraThread:
    """Keeps grabbing frames; `latest()` returns the newest (frame, time, frame_number)."""

    def __init__(self, source: str | int, name: str = "camera"):
        self.name = name
        self._cap = open_camera(source)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open {name} camera {source}")
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._t = 0.0
        self._count = 0
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while self._running:
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self._lock:
                self._frame, self._t, self._count = frame, time.monotonic(), self._count + 1

    def latest(self) -> tuple[np.ndarray | None, float, int]:
        with self._lock:
            return self._frame, self._t, self._count

    def wait_new(self, last_count: int, timeout: float = 1.0) -> tuple[np.ndarray | None, float, int]:
        """Block until a frame newer than last_count arrives (or timeout)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            frame, t, count = self.latest()
            if count != last_count and frame is not None:
                return frame, t, count
            time.sleep(0.002)
        return None, 0.0, last_count

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=1)
        self._cap.release()
