"""Set up and check the arm's front (side) and wrist cameras before recording.

Shows both cameras exactly as they will be recorded (640x480, rotation applied) with:
  fps, sharpness (higher = in focus; twist the lens for the max), brightness and
  % blown-out pixels, a thirds grid, and - once you have saved reference photos -
  how far each camera has moved since then (a bumped camera ruins a trained policy).

Usage (from the armproject folder):
    cameras.cmd

Keys:
    1 / 2   select front / wrist
    r       rotate the selected camera 90 degrees
    x       swap which camera number is front and which is wrist
    g       ghost: overlay the saved reference photo
    p       save reference photos of both views (do this once the setup is final)
    w       save the setup to arm/cameras.json (record.cmd / eval.cmd use it)
    q/Esc   quit
"""

import os
import time

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from arm import cameras as camcfg  # noqa: E402

WINDOW = "arm cameras"
ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, -90: cv2.ROTATE_90_COUNTERCLOCKWISE}
TILE = (560, 420)


def open_cam(index: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # after size/fps on Windows, like LeRobot
    return cap


def measure(frame: np.ndarray) -> tuple[float, float, float]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    centre = gray[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4]
    sharpness = float(cv2.Laplacian(centre, cv2.CV_64F).var())
    return sharpness, float(gray.mean()), float((gray >= 250).mean() * 100)


def shift_from(reference: np.ndarray, frame: np.ndarray) -> float:
    a = np.float32(cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY))
    b = np.float32(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    if a.shape != b.shape:
        return float("nan")
    (dx, dy), _ = cv2.phaseCorrelate(a, b)
    return float(np.hypot(dx, dy))


def main() -> None:
    cams = camcfg.load()
    names = list(cams)
    caps = {n: open_cam(cams[n]["index"]) for n in camcfg.open_order(cams)}
    camcfg.REFERENCE_DIR.mkdir(exist_ok=True)
    refs = {n: cv2.imread(str(camcfg.REFERENCE_DIR / f"{n}.png")) for n in names}
    selected, ghost, saved_msg, msg_until = names[0], False, "", 0.0
    fps = {n: 0.0 for n in names}
    last = {n: time.monotonic() for n in names}
    frames = {n: None for n in names}
    print(__doc__.split("Keys:")[1])

    while True:
        tiles = []
        for n in names:
            ok, frame = caps[n].read()
            now = time.monotonic()
            if ok:
                fps[n] = 0.9 * fps[n] + 0.1 / max(now - last[n], 1e-3)
                last[n] = now
                rot = cams[n]["rotation"]
                frames[n] = cv2.rotate(frame, ROTATE[rot]) if rot else frame
            frame = frames[n]
            if frame is None:
                tile = np.zeros((TILE[1], TILE[0], 3), np.uint8)
                cv2.putText(tile, f"{n}: camera {cams[n]['index']} - no picture", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                tiles.append(tile)
                continue
            sharp, bright, blown = measure(frame)
            ref = refs.get(n)
            moved = shift_from(ref, frame) if ref is not None else None
            view = frame.copy()
            if ghost and ref is not None and ref.shape == view.shape:
                view = cv2.addWeighted(view, 0.5, ref, 0.5, 0)
            h, w = view.shape[:2]
            for k in (1, 2):  # thirds grid
                cv2.line(view, (w * k // 3, 0), (w * k // 3, h), (255, 255, 255), 1)
                cv2.line(view, (0, h * k // 3), (w, h * k // 3), (255, 255, 255), 1)
            scale = min(TILE[0] / w, TILE[1] / h)
            view = cv2.resize(view, (int(w * scale), int(h * scale)))
            tile = np.zeros((TILE[1], TILE[0], 3), np.uint8)
            tile[: view.shape[0], : view.shape[1]] = view
            ok_light = 60 <= bright <= 190 and blown < 2
            lines = [
                (f"{n.upper()}  cam {cams[n]['index']} {cams[n].get('usb_id', '')[-8:]}  rot {cams[n]['rotation']}  {fps[n]:4.1f} fps",
                 (0, 255, 0) if fps[n] >= 27 else (0, 200, 255)),
                (f"sharpness {sharp:6.0f}", (255, 255, 255)),
                (f"brightness {bright:3.0f}  blown {blown:4.1f}%", (0, 255, 0) if ok_light else (0, 200, 255)),
            ]
            if moved is not None:
                lines.append((f"moved {moved:4.1f} px since reference", (0, 255, 0) if moved < 3 else (0, 0, 255)))
            for k, (text, colour) in enumerate(lines):
                cv2.putText(tile, text, (8, 22 + 22 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
                cv2.putText(tile, text, (8, 22 + 22 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 1)
            if n == selected:
                cv2.rectangle(tile, (0, 0), (TILE[0] - 1, TILE[1] - 1), (0, 200, 255), 3)
            tiles.append(tile)

        board = np.hstack(tiles)
        footer = "1/2 select  r rotate  x swap  g ghost  p save reference  w save setup  q quit"
        if time.monotonic() < msg_until:
            footer = saved_msg
        bar = np.zeros((34, board.shape[1], 3), np.uint8)
        cv2.putText(bar, footer, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.imshow(WINDOW, np.vstack([board, bar]))

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break
        if key in (ord("1"), ord("2")):
            selected = names[key - ord("1")]
        elif key == ord("r"):
            i = camcfg.ROTATIONS.index(cams[selected]["rotation"])
            cams[selected]["rotation"] = camcfg.ROTATIONS[(i + 1) % len(camcfg.ROTATIONS)]
        elif key == ord("x"):
            a, b = names
            cams[a]["index"], cams[b]["index"] = cams[b]["index"], cams[a]["index"]
            cams[a]["usb_id"], cams[b]["usb_id"] = cams[b].get("usb_id", ""), cams[a].get("usb_id", "")
            caps[a], caps[b] = caps[b], caps[a]
            frames[a], frames[b] = frames[b], frames[a]
        elif key == ord("g"):
            ghost = not ghost
        elif key == ord("p"):
            for n in names:
                if frames[n] is not None:
                    cv2.imwrite(str(camcfg.REFERENCE_DIR / f"{n}.png"), frames[n])
                    refs[n] = frames[n].copy()
            saved_msg, msg_until = f"saved reference photos to {camcfg.REFERENCE_DIR.name}/", time.monotonic() + 3
        elif key == ord("w"):
            camcfg.save(cams)
            saved_msg, msg_until = f"saved {camcfg.CONFIG_PATH.name}: {cams}", time.monotonic() + 4

    for cap in caps.values():
        cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
