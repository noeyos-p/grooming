"""
Silhouette IoU Probe — Phase 34 v1.1 Step 1.

기존 배치 결과(N=16)에 대해 rembg 기반 silhouette IoU를 재측정하고 v1.0의
head_bbox IoU와 비교한다. 목표: "실루엣 지표가 bbox보다 더 의미 있는가"
를 빠르게 확인.

입력:
  - 원본: ~/Downloads/IMG_7641.jpg (모든 샘플 공통)
  - 결과 URL: /tmp/anchor_inpaint_batch.jsonl (배치 러너 출력)

산출:
  - /tmp/silhouette_probe.jsonl — 샘플별 silhouette_iou(whole/head crop), bbox_iou 비교
  - /tmp/silhouette_fail/  — silhouette_iou_whole < 0.7 인 샘플의 마스크 PNG 저장
  - stdout: 요약 (anchor/gemini 각각 평균/분위수 + bbox-vs-silhouette 상관)

사용:
  cd backend && source venv/bin/activate
  python tests/run_silhouette_probe.py
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

import httpx
import numpy as np
import pillow_heif
from dotenv import load_dotenv
from google import genai
from PIL import Image

pillow_heif.register_heif_opener()

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
load_dotenv(_BACKEND_ROOT / ".env")

from services.inpaint_pipeline import _detect_full_head_bbox  # noqa: E402
from services.silhouette_iou import (  # noqa: E402
    crop_by_bbox,
    extract_foreground_mask,
    mask_to_png_bytes,
    silhouette_iou,
)

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")
BATCH_JSONL = Path("/tmp/anchor_inpaint_batch.jsonl")
OUTPUT_JSONL = Path("/tmp/silhouette_probe.jsonl")
FAIL_DIR = Path("/tmp/silhouette_fail")

FAIL_THRESHOLD = 0.70  # silhouette_iou_whole < 이 값 → 실패 샘플로 마스크 저장

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("silhouette_probe")


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=120.0)
        r.raise_for_status()
        return r.content


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 4),
        "median": round(statistics.median(values), 4),
        "p25": round(float(np.percentile(values, 25)), 4),
        "p75": round(float(np.percentile(values, 75)), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """단순 Pearson 상관 — bbox IoU vs silhouette IoU 연관도 파악용."""
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    xa = np.asarray(xs, dtype=np.float64)
    ya = np.asarray(ys, dtype=np.float64)
    if xa.std() == 0 or ya.std() == 0:
        return None
    return float(np.corrcoef(xa, ya)[0, 1])


async def _measure_one(
    row: dict,
    orig_bytes: bytes,
    orig_mask: np.ndarray,
    gemini_client,
) -> dict:
    result_url = row["result_url"]
    label = f"{row['pipeline']}/{row['breed_id']}/{row['style_id']}"
    logger.info("[probe] %s — download %s", label, result_url)
    result_bytes = await _download(result_url)

    logger.info("[probe] %s — rembg 전경 추출", label)
    res_mask = extract_foreground_mask(result_bytes)
    iou_whole = silhouette_iou(orig_mask, res_mask)

    # head_bbox 기반 crop IoU — 원본·결과 각각 head_bbox 재탐지 후 crop
    logger.info("[probe] %s — head_bbox 재탐지", label)
    head_o, head_r = await asyncio.gather(
        _detect_full_head_bbox(orig_bytes, gemini_client),
        _detect_full_head_bbox(result_bytes, gemini_client),
    )
    iou_head_crop = None
    if head_o is not None and head_r is not None:
        orig_head = crop_by_bbox(orig_mask, head_o)
        res_head = crop_by_bbox(res_mask, head_r)
        if orig_head.size > 0 and res_head.size > 0:
            iou_head_crop = silhouette_iou(orig_head, res_head)

    out = {
        "pipeline": row["pipeline"],
        "breed_id": row["breed_id"],
        "style_id": row["style_id"],
        "result_url": result_url,
        "bbox_iou_v1": row["head_shape_iou"],
        "bbox_iou_detect_fail": row["head_iou_detect_fail"],
        "silhouette_iou_whole": round(iou_whole, 4),
        "silhouette_iou_head_crop": (
            round(iou_head_crop, 4) if iou_head_crop is not None else None
        ),
        "head_bbox_orig": head_o,
        "head_bbox_res": head_r,
    }

    if iou_whole < FAIL_THRESHOLD:
        FAIL_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"{row['pipeline']}_{row['breed_id']}_{row['style_id']}"
        (FAIL_DIR / f"{stem}_orig_mask.png").write_bytes(mask_to_png_bytes(orig_mask))
        (FAIL_DIR / f"{stem}_res_mask.png").write_bytes(mask_to_png_bytes(res_mask))
        (FAIL_DIR / f"{stem}_result.jpg").write_bytes(result_bytes)
        out["fail_dump"] = str(FAIL_DIR / stem)
        logger.warning(
            "[probe] FAIL — %s silhouette_iou=%.3f < %.2f, 마스크 저장 %s",
            label, iou_whole, FAIL_THRESHOLD, out["fail_dump"],
        )

    return out


async def main_async() -> None:
    if not GOOGLE_API_KEY:
        raise SystemExit("GOOGLE_API_KEY 미설정")
    if not IMAGE_PATH.exists():
        raise SystemExit(f"원본 이미지 없음: {IMAGE_PATH}")
    if not BATCH_JSONL.exists():
        raise SystemExit(f"배치 결과 없음: {BATCH_JSONL}")

    rows = []
    for line in BATCH_JSONL.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if "error" in r or "result_url" not in r:
            continue
        rows.append(r)
    logger.info("[probe] batch rows: %d", len(rows))

    orig_bytes = IMAGE_PATH.read_bytes()
    with Image.open(io.BytesIO(orig_bytes)) as img:
        logger.info("[probe] 원본 크기: %s", img.size)

    logger.info("[probe] rembg 원본 전경 추출 (1회)")
    orig_mask = extract_foreground_mask(orig_bytes)
    logger.info(
        "[probe] 원본 전경 면적 비율: %.4f",
        float(orig_mask.sum() / orig_mask.size),
    )

    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSONL.write_text("")  # 덮어쓰기

    out_rows = []
    for idx, r in enumerate(rows, start=1):
        logger.info("=== [%d/%d] ===", idx, len(rows))
        try:
            out = await _measure_one(r, orig_bytes, orig_mask, gemini_client)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[probe] 예외 — %s/%s/%s: %s",
                           r["pipeline"], r["breed_id"], r["style_id"], exc)
            out = {
                "pipeline": r["pipeline"], "breed_id": r["breed_id"],
                "style_id": r["style_id"], "result_url": r["result_url"],
                "error": type(exc).__name__, "reason": str(exc),
            }
        out_rows.append(out)
        with OUTPUT_JSONL.open("a") as fp:
            fp.write(json.dumps(out, ensure_ascii=False) + "\n")

    # 요약
    def _ok(pipeline: str) -> list[dict]:
        return [r for r in out_rows if r.get("pipeline") == pipeline and "error" not in r]

    summary: dict = {}
    for pipeline in ("anchor", "gemini"):
        ok = _ok(pipeline)
        whole = [r["silhouette_iou_whole"] for r in ok]
        head_crop = [r["silhouette_iou_head_crop"] for r in ok if r.get("silhouette_iou_head_crop") is not None]
        bbox = [r["bbox_iou_v1"] for r in ok if not r.get("bbox_iou_detect_fail")]
        corr = _pearson(
            [r["bbox_iou_v1"] for r in ok if not r.get("bbox_iou_detect_fail")],
            [r["silhouette_iou_whole"] for r in ok if not r.get("bbox_iou_detect_fail")],
        )
        summary[pipeline] = {
            "n": len(ok),
            "silhouette_iou_whole": _stats(whole),
            "silhouette_iou_head_crop": _stats(head_crop),
            "bbox_iou_v1_valid": _stats(bbox),
            "bbox_iou_detect_fail_count": sum(1 for r in ok if r.get("bbox_iou_detect_fail")),
            "pearson_bbox_vs_silhouette_whole": (
                round(corr, 4) if corr is not None else None
            ),
            "fail_count_silhouette_lt_thresh": sum(
                1 for r in ok if r["silhouette_iou_whole"] < FAIL_THRESHOLD
            ),
            "fail_threshold": FAIL_THRESHOLD,
        }

    print(json.dumps({
        "output": str(OUTPUT_JSONL),
        "fail_dir": str(FAIL_DIR),
        "summary": summary,
        "rows": out_rows,
    }, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
