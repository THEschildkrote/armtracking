"""Run on the teammate's MacBook: YOLO on the relay's camera streams.

Setup (MacBook, same network as the Windows laptop - the iPhone hotspot):
    pip install ultralytics opencv-python
    python mac_yolo.py --host 172.20.10.4                 # all four streams
    python mac_yolo.py --host 172.20.10.4 --streams side  # just the side camera

The Windows laptop must be running relay.cmd; its console prints the address to use.
Each stream is MJPEG over HTTP, so anything that opens a URL works:
    cv2.VideoCapture("http://172.20.10.4:8080/side")
    YOLO("yolo11n.pt").predict("http://172.20.10.4:8080/side", stream=True)
    a browser: http://172.20.10.4:8080/   (all streams on one page)
Press q in any window to quit.
"""

import argparse
import threading
import time

import cv2
from ultralytics import YOLO


def reader(url: str, latest: dict, name: str, stop: threading.Event) -> None:
    """Keep only the newest frame, so YOLO never works on a stale backlog."""
    cap = None
    while not stop.is_set():
        if cap is None or not cap.isOpened():
            cap = cv2.VideoCapture(url)
            if not cap.isOpened():
                time.sleep(1)
                continue
        ok, frame = cap.read()
        if not ok:
            cap.release()
            cap = None
            time.sleep(0.5)
            continue
        latest[name] = frame


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="Windows laptop IP, e.g. 172.20.10.4")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--streams", nargs="+", default=["side", "wrist", "front", "eye"])
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--conf", type=float, default=0.4)
    args = parser.parse_args()

    model = YOLO(args.model)
    latest: dict = {}
    stop = threading.Event()
    for name in args.streams:
        url = f"http://{args.host}:{args.port}/{name}"
        threading.Thread(target=reader, args=(url, latest, name, stop), daemon=True).start()
        print("reading", url)

    try:
        while True:
            for name in args.streams:
                frame = latest.pop(name, None)
                if frame is None:
                    continue
                result = model.predict(frame, conf=args.conf, verbose=False)[0]
                cv2.imshow(name, result.plot())
                # result.boxes.xyxy / .cls / .conf hold the detections for your own logic
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        stop.set()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
