"""
Edit-zone change baseline 다중 이미지 재산정 (Phase 34.2 Step 2).

배경:
 - v1.0 edit_zone_change baseline_p25 = 29.942 (IMG_7641.jpg × 5 gemini 샘플 — 단일 이미지)
 - N=16 배치(2026-04-23) gemini p25 = 33.522 — 여전히 단일 이미지
 - 이미지 분포로 과적합 — 다중 이미지 × 대표 스타일로 재산정 필요

설계:
 - 이미지 5장: IMG_7641, IMG_2749, IMG_9712, IMG_9827, IMG_9999
 - 스타일 3개: maltese/teddy_cut, bichon/round_cut, pomeranian/bear_cut
 - pipeline: gemini 만 (edit 충분성 baseline 용도)
 - 각 이미지당 original silhouette mask 1회 추출 → 3스타일 재사용
 - `_one_sample(pipeline="gemini")` 기존 측정 경로 재사용

산출:
 - /tmp/edit_baseline_multi.jsonl — 샘플별 1 줄
 - stdout 요약 JSON: 전체 p25/p50/p75, 이미지·스타일별 세부 분포
 - edit_zone_change baseline_p25 새 값 후보

사용:
  cd backend && source venv/bin/activate
  python tests/run_edit_baseline_multi.py
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import statistics
import sys
from pathlib import Path

import cloudinary
import cloudinary.uploader
import numpy as np
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

IMAGE_PATHS = [
    Path("/Users/soyeon/Downloads/IMG_7641.jpg"),
    Path("/Users/soyeon/Downloads/IMG_2749.jpeg"),
    Path("/Users/soyeon/Downloads/IMG_9712.jpg"),
    Path("/Users/soyeon/Downloads/IMG_9827.jpg"),
    Path("/Users/soyeon/Downloads/IMG_9999.jpg"),
]

# 견종 중립 3개 스타일 — edit scope 차이 분산 확보
STYLES: list[tuple[str, str]] = [
    ("maltese", "teddy_cut"),
    ("bichon", "round_cut"),
    ("pomeranian", "bear_cut"),
]

OUTPUT_PATH = Path("/tmp/edit_baseline_multi.jsonl")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("edit_baseline_multi")


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 3),
        "median": round(statistics.median(values), 3),
        "p25": round(float(np.percentile(values, 25)), 3),
        "p50": round(float(np.percentile(values, 50)), 3),
        "p75": round(float(np.percentile(values, 75)), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


async def main_async() -> None:
    for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "GOOGLE_API_KEY", "FAL_KEY"):
        if not os.getenv(k):
            raise SystemExit(f"환경변수 미설정: {k}")
    for p in IMAGE_PATHS:
        if not p.exists():
            raise SystemExit(f"이미지 없음: {p}")
    _configure_cloudinary()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("")

    rows: list[dict] = []
    for img_idx, image_path in enumerate(IMAGE_PATHS, start=1):
        logger.info("=== [%d/%d] 이미지: %s ===", img_idx, len(IMAGE_PATHS), image_path.name)
        orig_bytes = image_path.read_bytes()
        with Image.open(io.BytesIO(orig_bytes)) as img:
            img_size = img.size
        image_url = _upload(
            orig_bytes, f"grooming-style/probe/edit-baseline-multi/{image_path.stem}"
        )
        logger.info("원본 업로드: %s (%s)", image_url, img_size)

        logger.info("원본 silhouette 마스크 추출 (rembg u2net, 1회)")
        orig_silhouette_mask = extract_foreground_mask(orig_bytes)

        for style_idx, (breed_id, style_id) in enumerate(STYLES, start=1):
            logger.info(
                "--- [%d/%d × %d/%d] %s / %s ---",
                img_idx, len(IMAGE_PATHS), style_idx, len(STYLES), breed_id, style_id,
            )
            row = await _one_sample(
                "gemini", breed_id, style_id, image_url,
                orig_bytes, img_size, orig_silhouette_mask,
            )
            row["image_name"] = image_path.name
            rows.append(row)
            with OUTPUT_PATH.open("a") as fp:
                fp.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 요약
    ok_rows = [r for r in rows if "error" not in r]
    edit_vals = [r["edit_zone_change"] for r in ok_rows]
    face_vals = [r["face_mae"] for r in ok_rows]
    silh_vals = [r["silhouette_iou_whole"] for r in ok_rows]

    # 이미지별 분포
    by_image: dict[str, list[float]] = {}
    for r in ok_rows:
        by_image.setdefault(r["image_name"], []).append(r["edit_zone_change"])

    # 스타일별 분포
    by_style: dict[str, list[float]] = {}
    for r in ok_rows:
        key = f"{r['breed_id']}/{r['style_id']}"
        by_style.setdefault(key, []).append(r["edit_zone_change"])

    summary = {
        "n_total": len(rows),
        "n_ok": len(ok_rows),
        "errors": [
            {"image": r.get("image_name"), "breed": r.get("breed_id"),
             "style": r.get("style_id"), "error": r["error"], "reason": r.get("reason")}
            for r in rows if "error" in r
        ],
        "edit_zone_change": _stats(edit_vals),
        "face_mae": _stats(face_vals),
        "silhouette_iou_whole": _stats(silh_vals),
        "by_image_edit_p25": {k: _stats(v).get("p25") for k, v in by_image.items()},
        "by_style_edit_p25": {k: _stats(v).get("p25") for k, v in by_style.items()},
        "baseline_p25_candidate": _stats(edit_vals).get("p25"),
    }

    print(json.dumps(
        {"output": str(OUTPUT_PATH), "summary": summary, "rows": rows},
        ensure_ascii=False,
    ))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
