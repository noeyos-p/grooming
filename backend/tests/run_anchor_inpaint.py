"""
Anchor-Inpaint v1.0 수동 검증 — 단일 이미지(`~/Downloads/IMG_7641.jpg`) 기준 4지표 dump.

측정 지표 (plan 문서 V1.0 검증 지표):
 1) Face MAE            — anchor hard_preserve 영역 픽셀 MAE (원본 vs 결과)
 2) Edit-zone change    — edit_mask(전신 실루엣 ∩ ¬앵커 dilate) 영역 |orig - result| 평균
 3) Silhouette IoU      — rembg(u2net) 전신 마스크 IoU (Phase 34.2 주 지표)
 4) Hard preserve ratio — hard_preserve_px / (W × H)
 (참고) Head shape IoU  — deprecated, 참고치로만 유지

추가 기록: mode, label_swapped, position_gate, r2 per part.

사용:
  cd backend && source venv/bin/activate
  python tests/run_anchor_inpaint.py --breed maltese --style teddy_cut

baseline(gemini) 모드:
  python tests/run_anchor_inpaint.py --pipeline gemini --breed maltese --style teddy_cut

stdout JSON 1 줄, stderr 로그.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import sys
from pathlib import Path

import cloudinary
import cloudinary.uploader
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

from services.anchor_inpaint_pipeline import (  # noqa: E402
    _build_bbox_anchor_mask,
    _build_two_layer_mask,
    _per_part_r2,
    _DEGRADED_R2_SCALE,
)
from services.evf_sam_geometry import QualityFailure, get_anchor_masks  # noqa: E402
from services.gemini_pipeline import run_gemini_pipeline  # noqa: E402
from services.inpaint_pipeline import _detect_full_head_bbox  # noqa: E402
from services.anchor_inpaint_pipeline import run_anchor_inpaint_pipeline  # noqa: E402
from services.silhouette_iou import extract_foreground_mask, silhouette_iou  # noqa: E402

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("run_anchor_inpaint")


def _configure_cloudinary() -> None:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )


def _upload(image_bytes: bytes, folder: str) -> str:
    res = cloudinary.uploader.upload(
        image_bytes, folder=folder, resource_type="image", format="jpg"
    )
    return res["secure_url"]


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=90.0)
        resp.raise_for_status()
        return resp.content


def _bbox_iou(a: dict, b: dict) -> float:
    ax1, ay1, ax2, ay2 = a["xmin"], a["ymin"], a["xmax"], a["ymax"]
    bx1, by1, bx2, by2 = b["xmin"], b["ymin"], b["xmax"], b["ymax"]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


def _masked_mae(orig: np.ndarray, result: np.ndarray, mask_bool: np.ndarray) -> float:
    if not mask_bool.any():
        return 0.0
    diff = np.abs(orig.astype(np.int16) - result.astype(np.int16)).astype(np.float32)
    return float(diff[mask_bool].mean())


async def _measure_metrics(
    orig_bytes: bytes,
    result_bytes: bytes,
    image_url: str,
    img_size: tuple[int, int],
) -> dict:
    """anchor geometry 재호출 + head_bbox 재탐지로 4지표 산출."""
    img_w, img_h = img_size
    orig_img = Image.open(io.BytesIO(orig_bytes)).convert("RGB")
    res_img = Image.open(io.BytesIO(result_bytes)).convert("RGB")
    if res_img.size != orig_img.size:
        res_img = res_img.resize(orig_img.size, Image.LANCZOS)
    orig_arr = np.asarray(orig_img, dtype=np.uint8)
    res_arr = np.asarray(res_img, dtype=np.uint8)

    # anchor geometry 재호출 (원본 기준)
    anchor = await get_anchor_masks(image_url, img_size)
    r2_scale = (
        _DEGRADED_R2_SCALE
        if anchor.mode == "degraded_single_eye"
        else 1.0
    )
    r2 = _per_part_r2(anchor.parts, r2_scale)

    # head_bbox — deprecated 참고치(head_shape_iou)용 탐지. 실패해도 main metric 은 보고됨
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    result_upload_url = _upload(result_bytes, "grooming-temp")
    try:
        head_orig, head_res = await asyncio.gather(
            _detect_full_head_bbox(orig_bytes, gemini_client),
            _detect_full_head_bbox(result_bytes, gemini_client),
        )
    except Exception as exc:
        logger.warning("head_bbox 탐지 실패(참고치) — %s", exc)
        head_orig, head_res = None, None

    # silhouette IoU (Phase 34.2 주 구조 안정 지표) — edit_mask 합성에도 사용
    orig_silhouette_mask = extract_foreground_mask(orig_bytes)
    res_silhouette_mask = extract_foreground_mask(result_bytes)
    silh_iou_whole = silhouette_iou(orig_silhouette_mask, res_silhouette_mask)

    # anchor hard preserve + edit_mask 합성 (Phase 34.3 — 전신 실루엣 기반)
    hard = _build_bbox_anchor_mask(anchor.parts, img_size)
    edit = _build_two_layer_mask(
        anchor.parts, orig_silhouette_mask, img_size, r2
    )

    hard_bool = hard >= 128
    edit_bool = edit >= 128

    face_mae = _masked_mae(orig_arr, res_arr, hard_bool)
    edit_change = _masked_mae(orig_arr, res_arr, edit_bool)

    # Head shape IoU (deprecated — silhouette_iou_whole 이 주 지표, 참고치로만 기록)
    if head_orig is None or head_res is None:
        head_iou = 0.0
        head_iou_detect_fail = True
    else:
        head_iou = _bbox_iou(head_orig, head_res)
        head_iou_detect_fail = False

    preserve_ratio = float(hard_bool.sum() / (img_w * img_h))

    return {
        "mode": anchor.mode,
        "label_swapped": anchor.label_swapped,
        "position_gate_ok": anchor.position_gate_ok,
        "part_r2": r2,
        "parts_bbox": {k: list(p.bbox) for k, p in anchor.parts.items()},
        "metrics": {
            "face_mae": round(face_mae, 3),
            "edit_zone_change": round(edit_change, 3) if edit_change is not None else None,
            "silhouette_iou_whole": round(silh_iou_whole, 4),
            "head_shape_iou": round(head_iou, 4),
            "head_iou_detect_fail": head_iou_detect_fail,
            "hard_preserve_area_ratio": round(preserve_ratio, 5),
        },
        "head_bbox": {
            "original": head_orig,
            "result": head_res,
        },
        "result_url": result_upload_url,
    }


async def _run(pipeline: str, breed_id: str, style_id: str) -> dict:
    for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "GOOGLE_API_KEY", "FAL_KEY"):
        if not os.getenv(k):
            raise SystemExit(f"환경변수 미설정: {k}")
    if not IMAGE_PATH.exists():
        raise SystemExit(f"이미지 없음: {IMAGE_PATH}")
    _configure_cloudinary()

    orig_bytes = IMAGE_PATH.read_bytes()
    with Image.open(io.BytesIO(orig_bytes)) as img:
        img_size = img.size
    logger.info("원본: %s %s", IMAGE_PATH.name, img_size)

    image_url = _upload(orig_bytes, "grooming-style/probe/anchor-inpaint")
    logger.info("원본 업로드: %s", image_url)

    logger.info("파이프라인 실행: %s (breed=%s, style=%s)", pipeline, breed_id, style_id)
    if pipeline == "anchor":
        result_url = await run_anchor_inpaint_pipeline(image_url, breed_id, style_id)
    elif pipeline == "gemini":
        result_url = await run_gemini_pipeline(image_url, breed_id, style_id)
    else:
        raise SystemExit(f"알 수 없는 pipeline: {pipeline}")
    logger.info("결과 URL: %s", result_url)

    result_bytes = await _download(result_url)
    metrics = await _measure_metrics(orig_bytes, result_bytes, image_url, img_size)
    metrics["pipeline"] = pipeline
    metrics["breed_id"] = breed_id
    metrics["style_id"] = style_id
    metrics["image_size"] = list(img_size)
    metrics["image_url"] = image_url
    metrics["generated_url"] = result_url
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", choices=["anchor", "gemini"], default="anchor")
    ap.add_argument("--breed", required=True, dest="breed_id")
    ap.add_argument("--style", required=True, dest="style_id")
    args = ap.parse_args()

    try:
        result = asyncio.run(_run(args.pipeline, args.breed_id, args.style_id))
    except QualityFailure as exc:
        print(json.dumps({"error": "QualityFailure", "reason": str(exc)}, ensure_ascii=False))
        sys.exit(3)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
