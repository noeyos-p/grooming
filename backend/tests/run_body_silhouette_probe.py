"""Body silhouette 시각 검증 — Phase B1.

표준 이미지에 extract_foreground_mask(rembg/u2net)를 적용해
새 edit 마스크의 후보가 될 전신 실루엣을 PNG로 저장한다.
spot-check 항목:
  - 머리·몸통·다리·꼬리 모두 포함되었는가
  - 배경 누수가 있는가 (잔디·그림자·반사 영역)
  - 코·입 영역이 후속 hard_preserve dilate(r2)에 잘 들어갈 만큼 충분한가
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from services.image_utils import _convert_to_jpeg_if_needed  # noqa: E402
from services.silhouette_iou import extract_foreground_mask, mask_to_png_bytes  # noqa: E402

IMAGE_PATH = Path.home() / "Downloads" / "IMG_7641.jpg"
OUT_DIR = Path(__file__).resolve().parent / "_out"


def main() -> int:
    if not IMAGE_PATH.exists():
        print(f"[probe] 표준 이미지 없음: {IMAGE_PATH}")
        return 1

    OUT_DIR.mkdir(exist_ok=True)
    raw_bytes = IMAGE_PATH.read_bytes()
    image_bytes, converted = _convert_to_jpeg_if_needed(raw_bytes)
    print(
        f"[probe] 입력: {IMAGE_PATH} (raw {len(raw_bytes)} bytes "
        f"→ {len(image_bytes)} bytes, heic_converted={converted})"
    )

    # 1) 전신 실루엣
    body_mask = extract_foreground_mask(image_bytes)
    h, w = body_mask.shape
    coverage = float(body_mask.sum()) / (h * w)
    print(f"[probe] silhouette: {w}x{h}, foreground coverage={coverage:.3f}")

    # 2) 원본 위에 silhouette overlay (반투명 빨강)
    orig = Image.open(IMAGE_PATH).convert("RGB")
    if orig.size != (w, h):
        orig = orig.resize((w, h), Image.LANCZOS)
    orig_arr = np.asarray(orig, dtype=np.uint8).copy()
    red_overlay = np.zeros_like(orig_arr)
    red_overlay[..., 0] = 255
    alpha = 0.35
    blended = orig_arr.copy()
    blended[body_mask] = (
        (1 - alpha) * orig_arr[body_mask] + alpha * red_overlay[body_mask]
    ).astype(np.uint8)

    # 3) 저장
    silhouette_png = OUT_DIR / "body_silhouette.png"
    silhouette_png.write_bytes(mask_to_png_bytes(body_mask))
    overlay_png = OUT_DIR / "body_silhouette_overlay.png"
    Image.fromarray(blended, mode="RGB").save(overlay_png, format="PNG")

    print(f"[probe] saved: {silhouette_png}")
    print(f"[probe] saved: {overlay_png}")
    print(
        "[probe] spot-check: "
        "1) 다리·꼬리 포함 2) 배경 누수 3) 머리·몸통 경계"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
