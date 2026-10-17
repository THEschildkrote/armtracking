"""Camera relay: open every camera ONCE on this laptop and re-serve it to any number of viewers.

Streams (MJPEG over HTTP, readable by OpenCV, ultralytics/YOLO, a browser, VLC):
    http://<laptop-ip>:8080/            index page with all streams
    http://<laptop-ip>:8080/side        arm side/front camera (USB, longer lens)
    http://<laptop-ip>:8080/wrist       arm wrist camera (USB)
    http://<laptop-ip>:8080/eye         glasses eye camera (USB, Board A)
    http://<laptop-ip>:8080/front       glasses front camera (Board B over Wi-Fi, un-mirrored)
    http://<laptop-ip>:8080/<name>.jpg  single latest frame

Board B accepts only one viewer, and the USB cameras only one program, so the
relay is that one; everything else (a teammate's MacBook running YOLO, gaze.cmd
with --eye/--scene pointing here) reads from the relay instead.

Usage (from the armproject folder):
    relay.cmd                         # all four, 15 fps, JPEG quality 75
    relay.cmd --only side wrist       # just some
    relay.cmd --fps 10 --quality 60   # lighter on the phone hotspot

The first run, Windows asks whether Python may accept network connections - allow it
on private networks, or the MacBook can't connect. Both machines must be on the same
network (the iPhone hotspot; Harvard Wi-Fi blocks device-to-device traffic).

Note: LeRobot (record.cmd / eval.cmd / teleop) needs the arm cameras itself - stop the
relay (or run it with --only eye front) while recording or evaluating.
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "fflags;nobuffer|flags;low_delay")
import cv2  # noqa: E402

from arm import cameras as armcams  # noqa: E402
from gaze.capture import FRONT_MIRRORED  # noqa: E402

BOUNDARY = "frame"


class Source:
    """One camera: a capture thread that keeps the newest frame JPEG-encoded and shared."""

    def __init__(self, name: str, opener, mirror: bool, quality: int, max_fps: float, rotation: int = 0):
        self.name, self._opener, self._mirror = name, opener, mirror
        self._rotate = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, -90: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(rotation)
        self._quality, self._period = quality, 1.0 / max_fps
        self.jpeg: bytes | None = None
        self.count = 0
        self.size = (0, 0)
        self.fps = 0.0
        self._cond = threading.Condition()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        cap, last, fails = None, 0.0, 0
        while True:
            if cap is None or not cap.isOpened():
                cap = self._opener()
                if not cap.isOpened():
                    time.sleep(2)
                    continue
            ok, frame = cap.read()
            if not ok:
                fails += 1
                if fails >= 3:  # stalled: reopen
                    cap.release()
                    cap, fails = None, 0
                    time.sleep(0.5)
                continue
            fails = 0
            now = time.monotonic()
            if now - last < self._period:
                continue  # cap the relay rate; the camera keeps running at its own rate
            self.fps = 0.8 * self.fps + 0.2 / max(now - last, 1e-3) if last else 0.0
            last = now
            if self._mirror:
                frame = cv2.flip(frame, 1)
            if self._rotate is not None:
                frame = cv2.rotate(frame, self._rotate)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
            if ok:
                with self._cond:
                    self.jpeg, self.count, self.size = buf.tobytes(), self.count + 1, (frame.shape[1], frame.shape[0])
                    self._cond.notify_all()

    def wait_frame(self, last_count: int, timeout: float = 2.0) -> tuple[bytes | None, int]:
        with self._cond:
            self._cond.wait_for(lambda: self.count != last_count, timeout)
            return self.jpeg, self.count


def usb_opener(index: int, mjpg: bool):
    def open_():
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        if mjpg:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # after size/fps on Windows
        return cap
    return open_


def url_opener(url: str):
    def open_():
        return cv2.VideoCapture(url, cv2.CAP_FFMPEG,
                                [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2000])
    return open_


def make_handler(sources: dict[str, Source]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            path = self.path.strip("/").split("?")[0]
            if path == "":
                return self._index()
            snapshot = path.endswith(".jpg")
            name = path[:-4] if snapshot else path
            src = sources.get(name)
            if src is None:
                self.send_error(404, f"no stream {name!r}; have {', '.join(sources)}")
                return
            if snapshot:
                jpeg, _ = src.wait_frame(-1, 0.1) if src.jpeg is None else (src.jpeg, src.count)
                if not jpeg:
                    self.send_error(503, "no frame yet")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg)
                return
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            count = -1
            try:
                while True:
                    jpeg, count = src.wait_frame(count)
                    if jpeg is None:
                        continue
                    self.wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # viewer went away

        def _index(self):
            rows = "".join(
                f"<figure><img src='/{n}' width=320><figcaption>{n} &nbsp; "
                f"{s.size[0]}x{s.size[1]} &nbsp; ~{s.fps:.0f} fps &nbsp; <code>/{n}</code></figcaption></figure>"
                for n, s in sources.items())
            html = (f"<html><head><title>camera relay</title><style>body{{font-family:sans-serif;background:#111;color:#ddd}}"
                    f"figure{{display:inline-block;margin:8px}}</style></head><body><h3>camera relay</h3>{rows}</body></html>")
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def local_ips() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--front-url", default="http://172.20.10.2/", help="Board B stream")
    parser.add_argument("--only", nargs="+", choices=["side", "wrist", "eye", "front"])
    parser.add_argument("--fps", type=float, default=15, help="Max frames per second relayed per camera")
    parser.add_argument("--quality", type=int, default=75, help="JPEG quality 1-100")
    args = parser.parse_args()
    wanted = args.only or ["wrist", "side", "eye", "front"]

    devices = armcams.video_devices() or armcams.video_devices()  # the device listing can fail once; retry
    if not devices:
        raise SystemExit("Could not list cameras (ffmpeg) - try again.")
    index_of = {usb: i for i, (_, usb) in enumerate(devices)}
    arm_cfg = armcams.load()
    eye_index = next((i for i, (n, _) in enumerate(devices) if n == "openiristracker"), None)

    sources: dict[str, Source] = {}
    # wrist before side: the side camera is uncompressed-only and, opened first, starves the wrist camera
    for name in [n for n in ("wrist", "side", "eye", "front") if n in wanted]:
        if name in ("wrist", "side"):
            cfg = arm_cfg["wrist" if name == "wrist" else "front"]
            idx = index_of.get(cfg.get("usb_id"))
            if idx is None:  # never fall back to a number: it may now belong to a different camera
                print(f"{name} camera ({cfg.get('usb_id')}) not plugged in - skipping")
                continue
            # same rotation as LeRobot records with (set in cameras.cmd, saved in arm/cameras.json)
            sources[name] = Source(name, usb_opener(idx, mjpg=True), False, args.quality, args.fps, cfg.get("rotation", 0))
            time.sleep(1.0)  # let it claim its bandwidth before the next one opens
        elif name == "eye":
            if eye_index is None:
                print("eye camera (openiristracker) not found - skipping")
                continue
            sources[name] = Source(name, usb_opener(eye_index, mjpg=False), False, args.quality, args.fps)
        else:
            sources[name] = Source(name, url_opener(args.front_url), FRONT_MIRRORED, args.quality, args.fps)

    if not sources:
        raise SystemExit("No cameras to relay.")
    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(sources))
    print("Camera relay running. Open on the other machine (same network):", flush=True)
    for ip in local_ips() or ["<this-laptop-ip>"]:
        print(f"  http://{ip}:{args.port}/   ->  " + "  ".join(f"/{n}" for n in sources))
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
