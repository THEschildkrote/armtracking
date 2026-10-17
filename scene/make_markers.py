"""Make a printable A4 sheet with the 4 table markers (ids 0-3).

Writes markers_A4.pdf (exact physical size - print at 100% / "actual size",
not "fit to page") and a PNG preview. A 100 mm ruler on the sheet lets you
check the printer did not scale it; if it measures wrong, set
marker_size_mm in scene/config.json to what you actually measure.

Usage (from the armproject folder):
    python -m scene.make_markers
"""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from scene.markers import DICTIONARY, TableConfig

DPI = 300
A4_MM = (210, 297)


def mm(v: float) -> int:
    return int(round(v / 25.4 * DPI))


def main() -> None:
    config = TableConfig.load()
    size = config.marker_size_mm
    page = np.full((mm(A4_MM[1]), mm(A4_MM[0])), 255, np.uint8)
    dictionary = cv2.aruco.getPredefinedDictionary(DICTIONARY)

    # 2 x 2 grid of markers, each with a white quiet zone and a label
    cell_w, cell_h = A4_MM[0] / 2, (A4_MM[1] - 40) / 2
    for i, marker_id in enumerate(sorted(config.markers)[:4]):
        img = cv2.aruco.generateImageMarker(dictionary, marker_id, mm(size))
        cx = cell_w * (i % 2) + cell_w / 2
        cy = 30 + cell_h * (i // 2) + cell_h / 2
        x0, y0 = mm(cx - size / 2), mm(cy - size / 2)
        page[y0 : y0 + img.shape[0], x0 : x0 + img.shape[1]] = img
        pad = 8
        label = f"id {marker_id} - this edge away from you"
        cv2.putText(page, label, (mm(cx - size / 2 - pad), mm(cy - size / 2 - pad - 3)), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 3)
        # light cut guide around each marker
        cv2.rectangle(page, (mm(cx - size / 2 - pad), mm(cy - size / 2 - pad)),
                      (mm(cx + size / 2 + pad), mm(cy + size / 2 + pad)), 190, 2)

    # 100 mm ruler to check print scale
    rx, ry = mm(55), mm(15)
    cv2.line(page, (rx, ry), (rx + mm(100), ry), 0, 4)
    for k in range(11):
        tick = mm(4 if k % 5 else 7)
        cv2.line(page, (rx + mm(10 * k), ry - tick), (rx + mm(10 * k), ry), 0, 3)
    cv2.putText(page, "100 mm - measure me", (rx + mm(105), ry), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)

    out = Path("markers_A4.pdf")
    Image.fromarray(page).save(out, resolution=DPI)
    cv2.imwrite("markers_A4_preview.png", cv2.resize(page, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA))
    print(f"wrote {out.resolve()} - print at 100% (actual size)")


if __name__ == "__main__":
    main()
