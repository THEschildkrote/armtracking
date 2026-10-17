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

# Board B (OpenIris over Wi-Fi) sends its picture mirrored left-right; un-mirror it everywhere so the
# table's ArUco markers decode and screen/table left is image left.
FRONT_MIRRORED = True


def is_raw_board_stream(url: str) -> bool:
    """True for an OpenIris board's own stream (root path), False for relay streams like /front."""
    from urllib.parse import urlparse

    return urlparse(url).path in ("", "/")


EYE_CAMERA_NAME = "openiristracker"  # Board A's USB camera name
BOARD_B_MAC = "10-b4-1d-e9-fe-1c"  # Board B's Wi-Fi address: finds its IP on whichever hotspot it joined


def resolve(source: str | int, which: str) -> str:
    """'auto' -> Board A's camera number (eye) or Board B's stream URL (front); anything else as given.

    Numbers change whenever USB devices come and go, and Board B's IP changes with the hotspot,
    so the gaze tools default to auto."""
    source = str(source)
    if source != "auto":
        return source
    if which == "eye":
        from arm import cameras as armcams

        devices = armcams.video_devices() or armcams.video_devices()
        index = next((i for i, (name, _) in enumerate(devices) if name == EYE_CAMERA_NAME), None)
        if index is None:
            raise RuntimeError("Eye camera (Board A, openiristracker) not plugged in")
        return str(index)
    import re
    import subprocess

    def lookup():
        table = subprocess.run(["arp", "-a"], capture_output=True, text=True).stdout.lower()
        return re.search(r"(\d+\.\d+\.\d+\.\d+)\s+" + BOARD_B_MAC, table)

    m = lookup()  # asks no one over HTTP: Board B serves one viewer at a time
    if not m:
        # On a hotspot the laptop doesn't run (the iPhone's), the laptop only learns Board B's address
        # after talking to it: ping the hotspot ranges once to fill the ARP table, then look again.
        from concurrent.futures import ThreadPoolExecutor

        ips = [f"172.20.10.{i}" for i in range(2, 15)] + [f"192.168.137.{i}" for i in range(2, 255)]
        with ThreadPoolExecutor(64) as pool:
            list(pool.map(lambda ip: subprocess.run(["ping", "-n", "1", "-w", "400", ip], capture_output=True), ips))
        m = lookup()
    if not m:
        raise RuntimeError("Board B isn't on the hotspot (not in the laptop's ARP table) - power it and wait ~30 s")
    return f"http://{m.group(1)}/"


def open_camera(source: str | int) -> cv2.VideoCapture:
    source = str(source)
    if source.isdigit():
        cap = cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
        if not cap.isOpened():  # DirectShow sometimes refuses a board after a replug
            cap = cv2.VideoCapture(int(source), cv2.CAP_MSMF)
        return cap
    # Network stream (Board B's MJPEG over HTTP): our own reader. On a weak Wi-Fi link corrupt packets made
    # FFmpeg abort the whole process (an assertion in libavformat); this one just skips a frame it can't decode.
    return MjpegCapture(source)


SOI, EOI = bytes([0xFF, 0xD8]), bytes([0xFF, 0xD9])  # JPEG start / end markers


class MjpegCapture:
    """Minimal cv2.VideoCapture stand-in for an HTTP multipart MJPEG stream: read() -> (ok, frame).

    JPEGs are cut out of the byte stream between their start (FFD8) and end (FFD9) markers and decoded one by
    one; a frame that won't decode is dropped. read() fails after `read_timeout` s without a good frame, so
    CameraThread reconnects as before."""

    def __init__(self, url: str, open_timeout: float = 5.0, read_timeout: float = 2.0):
        import urllib.request

        self.read_timeout = read_timeout
        self._buf = b""
        try:
            self._resp = urllib.request.urlopen(url, timeout=open_timeout)
        except Exception:
            self._resp = None

    def isOpened(self) -> bool:  # noqa: N802 - mirrors cv2.VideoCapture
        return self._resp is not None

    def read(self):
        if self._resp is None:
            return False, None
        deadline = time.monotonic() + self.read_timeout
        while time.monotonic() < deadline:
            start = self._buf.find(SOI)
            end = self._buf.find(EOI, start + 2) if start >= 0 else -1
            if start >= 0 and end >= 0:
                jpg, self._buf = self._buf[start:end + 2], self._buf[end + 2:]
                frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    return True, frame
                continue  # corrupt frame: skip it
            if len(self._buf) > 2_000_000:  # never found an end marker: start over
                self._buf = self._buf[-1000:]
            try:
                chunk = self._resp.read1(65536) if hasattr(self._resp, "read1") else self._resp.read(4096)
            except Exception:
                return False, None
            if not chunk:
                return False, None
            self._buf += chunk
        return False, None

    def release(self) -> None:
        try:
            if self._resp is not None:
                self._resp.close()
        finally:
            self._resp = None


class CameraThread:
    """Keeps grabbing frames; `latest()` returns the newest (frame, time, frame_number).

    Network streams (the Wi-Fi front camera) reconnect by themselves when they stall.
    """

    def __init__(self, source: str | int, name: str = "camera", mirror: bool | None = None):
        self.name = name
        self._spec, self._kind = source, ("eye" if name == "eye" else "front")  # re-resolved on reconnect
        source = resolve(source, self._kind)
        print(f"{name} camera: {source}", flush=True)
        self._source = source
        self._network = not str(source).isdigit()
        # Only Board B's own stream (http://<ip>/) is mirrored; relay streams (http://<ip>:8080/front) are not.
        self._mirror = (self._network and FRONT_MIRRORED and is_raw_board_stream(str(source))) if mirror is None else mirror
        self._cap = open_camera(source)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open {name} camera {source}")
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._t = 0.0
        self._count = 0
        self.reconnects = 0
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        failures = 0
        while self._running:
            ok, frame = self._cap.read()
            if not ok:
                failures += 1
                # A stalled Wi-Fi stream, or a USB board that reset (it comes back, maybe under a new
                # number): drop it and connect again.
                if failures >= (2 if self._network else 100):
                    self._cap.release()
                    time.sleep(0.5)
                    try:
                        self._source = resolve(self._spec, self._kind)
                        self._cap = open_camera(self._source)
                    except RuntimeError:  # not back on USB / the hotspot yet: try again shortly
                        time.sleep(0.5)
                        self._cap = cv2.VideoCapture()
                    self.reconnects += 1
                    failures = 0
                else:
                    time.sleep(0.01)
                continue
            failures = 0
            if self._mirror:
                frame = cv2.flip(frame, 1)
            with self._lock:
                self._frame, self._t, self._count = frame, time.monotonic(), self._count + 1

    def age(self) -> float:
        """Seconds since the newest frame arrived (inf before the first one)."""
        with self._lock:
            return time.monotonic() - self._t if self._count else float("inf")

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
