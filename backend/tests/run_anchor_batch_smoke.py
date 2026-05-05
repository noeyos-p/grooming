"""
Anchor-Inpaint 배치 스크립트 측정 경로 스모크 테스트.

Phase 34.2에서 `_measure`에 silhouette_iou_whole을 통합한 후,
최소 샘플(anchor 1 + gemini 1)로 필드가 올바르게 채워지는지 검증한다.

- 이미지: ~/Downloads/IMG_7641.jpg
- 조합: maltese/teddy_cut 만 (anchor + gemini)
- 확인: silhouette_iou_whole 필드가 0.0~1.0 범위의 float로 존재

사용:
  cd backend && source venv/bin/activate
  python tests/run_anchor_batch_smoke.py
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
from pathlib import Path

import cloudinary
import cloudinary.uploader
import pillow_heif
from dotenv import load_dotenv
from PIL import Image

pillow_heif.register_heif_opener()

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
load_dotenv(_BACKEND_ROOT / ".env")

from tests.run_anchor_inpaint_batch import (  # noqa: E402
    _configure_cloudinary,
    _one_sample,
    _upload,
)
from services.silhouette_iou import extract_foreground_mask  # noqa: E402

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("smoke")


async def main_async() -> None:
    for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "GOOGLE_API_KEY", "FAL_KEY"):
        if not os.getenv(k):
            raise SystemExit(f"환경변수 미설정: {k}")
    _configure_cloudinary()

    orig_bytes = IMAGE_PATH.read_bytes()
    with Image.open(io.BytesIO(orig_bytes)) as img:
        img_size = img.size
    image_url = _upload(orig_bytes, "grooming-style/probe/smoke")
    logger.info("원본 업로드: %s (%s)", image_url, img_size)

    logger.info("원본 silhouette 마스크 추출 (rembg)")
    orig_mask = extract_foreground_mask(orig_bytes)

    results = []
    for pipeline in ("anchor", "gemini"):
        logger.info("=== %s ===", pipeline)
        row = await _one_sample(
            pipeline, "maltese", "teddy_cut", image_url,
            orig_bytes, img_size, orig_mask,
        )
        results.append(row)

    # 필드 검증
    for r in results:
        if "error" in r:
            logger.error("실패: %s", r)
            continue
        silh = r.get("silhouette_iou_whole")
        assert isinstance(silh, float), f"silhouette_iou_whole not float: {silh!r}"
        assert 0.0 <= silh <= 1.0, f"silhouette_iou_whole out of range: {silh}"
        logger.info(
            "[OK] %s — silhouette_iou_whole=%.4f face_mae=%.3f edit=%.3f",
            r["pipeline"], silh, r["face_mae"], r["edit_zone_change"],
        )

    print(json.dumps({"rows": results}, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
