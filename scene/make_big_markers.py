"""Printable big table markers (ids 4-7, 150 mm) for the head camera: one per US Letter page.

Board B's 240x240 picture shows the 50 mm markers at ~8-10 px - too small to decode. At 150 mm they are
~40-45 px. Writes markers_big_letter.pdf (4 pages) - print at 100% / "actual size", not "fit to page",
and check the 100 mm ruler. Then lay them flat around the board (see HARDWARE / VENUE notes) and run
    python -m scene.add_big_markers
with the side camera locked to add their positions to scene/config.json automatically.

Usage (from the armproject folder):
    python -m scene.make_big_markers
"""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from scene.markers import DICTIONARY

DPI = 300
LETTER_MM = (215.9, 279.4)
SIZE_MM = 150.0
IDS = (4, 5, 6, 7)


def mm(v: float) -> int:
    return int(round(v / 25.4 * DPI))


def page_for(marker_id: int) -> np.ndarray:
    page = np.full((mm(LETTER_MM[1]), mm(LETTER_MM[0])), 255, np.uint8)
    img = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(DICTIONARY), marker_id, mm(SIZE_MM))
    x0, y0 = mm((LETTER_MM[0] - SIZE_MM) / 2), mm(70)
    page[y0 : y0 + img.shape[0], x0 : x0 + img.shape[1]] = img
    cv2.putText(page, f"id {marker_id}   ({SIZE_MM:.0f} mm)", (mm(20), mm(40)), cv2.FONT_HERSHEY_SIMPLEX, 3.0, 0, 8)
    cv2.putText(page, "THIS EDGE AWAY FROM YOU", (x0, y0 - mm(6)), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 0, 6)
    cv2.arrowedLine(page, (mm(LETTER_MM[0] / 2), y0 - mm(10)), (mm(LETTER_MM[0] / 2), y0 - mm(22)), 0, 8, tipLength=0.4)
    # 100 mm ruler to check the printer did not scale it
    rx, ry = mm(55), mm(250)
    cv2.line(page, (rx, ry), (rx + mm(100), ry), 0, 4)
    for k in range(11):
        tick = mm(4 if k % 5 else 7)
        cv2.line(page, (rx + mm(10 * k), ry - tick), (rx + mm(10 * k), ry), 0, 3)
    cv2.putText(page, "100 mm - measure me", (rx, ry + mm(10)), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 4)
    return page


def main() -> None:
    pages = [Image.fromarray(page_for(i)) for i in IDS]
    out = Path("markers_big_letter.pdf")
    pages[0].save(out, resolution=DPI, save_all=True, append_images=pages[1:])
    print(f"wrote {out.resolve()} - {len(pages)} pages, print at 100% (actual size)")


if __name__ == "__main__":
    main()
