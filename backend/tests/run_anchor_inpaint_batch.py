"""
Anchor-Inpaint v1.0 배치 수동 검증 — 같은 IMG_7641.jpg 에 여러 breed/style 조합을 적용.

목적:
 - Task #8: 11개 견종 대표 스타일로 anchor 경로 돌려 4지표 분포 확보
 - Task #4: gemini baseline 을 5~7 샘플로 돌려 edit_zone_change p25 임계 산정

산출:
 - JSONL: /tmp/anchor_inpaint_batch.jsonl — 샘플별 1 줄
 - stdout 요약 JSON: 평균/중앙값/분위수

사용:
  cd backend && source venv/bin/activate
  python tests/run_anchor_inpaint_batch.py
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import statistics
import sys
from datetime import datetime
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
    run_anchor_inpaint_pipeline,
)
from services.evf_sam_geometry import QualityFailure, get_anchor_masks  # noqa: E402
from services.gemini_pipeline import run_gemini_pipeline  # noqa: E402
from services.inpaint_pipeline import _detect_full_head_bbox  # noqa: E402
from services.silhouette_iou import (  # noqa: E402
    extract_foreground_mask,
    silhouette_iou,
)
from services.style_prompts import get_all_breeds  # noqa: E402

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")
OUTPUT_PATH = Path("/tmp/anchor_inpaint_batch.jsonl")

# baseline(gemini) 샘플링 대상 — plan: 5~10장
BASELINE_BREEDS = ["maltese", "poodle", "bichon", "pomeranian", "shih_tzu"]

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("batch")


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
        resp = await client.get(url, timeout=120.0)
        resp.raise_for_status()
        return resp.content


def _bbox_iou(a: dict, b: dict) -> float:
    ix1 = max(a["xmin"], b["xmin"])
    iy1 = max(a["ymin"], b["ymin"])
    ix2 = min(a["xmax"], b["xmax"])
    iy2 = min(a["ymax"], b["ymax"])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = max(0, a["xmax"] - a["xmin"]) * max(0, a["ymax"] - a["ymin"])
    area_b = max(0, b["xmax"] - b["xmin"]) * max(0, b["ymax"] - b["ymin"])
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


def _masked_mae(orig: np.ndarray, result: np.ndarray, mask_bool: np.ndarray) -> float:
    if not mask_bool.any():
        return 0.0
    return float(
        np.abs(orig.astype(np.int16) - result.astype(np.int16))[mask_bool].mean()
    )


async def _measure(
    orig_bytes: bytes,
    result_bytes: bytes,
    image_url: str,
    img_size: tuple[int, int],
    orig_silhouette_mask: np.ndarray,
) -> dict:
    orig = Image.open(io.BytesIO(orig_bytes)).convert("RGB")
    res = Image.open(io.BytesIO(result_bytes)).convert("RGB")
    if res.size != orig.size:
        res = res.resize(orig.size, Image.LANCZOS)
    orig_arr = np.asarray(orig, dtype=np.uint8)
    res_arr = np.asarray(res, dtype=np.uint8)

    anchor = await get_anchor_masks(image_url, img_size)
    r2_scale = _DEGRADED_R2_SCALE if anchor.mode == "degraded_single_eye" else 1.0
    r2 = _per_part_r2(anchor.parts, r2_scale)

    client = genai.Client(api_key=GOOGLE_API_KEY)
    head_o, head_r = await asyncio.gather(
        _detect_full_head_bbox(orig_bytes, client),
        _detect_full_head_bbox(result_bytes, client),
    )
    if head_o is None:
        raise RuntimeError("head_bbox(원본) 탐지 실패")

    hard = _build_bbox_anchor_mask(anchor.parts, img_size)
    edit = _build_two_layer_mask(anchor.parts, head_o, img_size, r2)
    hard_b = hard >= 128
    edit_b = edit >= 128

    face_mae = _masked_mae(orig_arr, res_arr, hard_b)
    edit_change = _masked_mae(orig_arr, res_arr, edit_b)
    preserve_ratio = float(hard_b.sum() / (img_size[0] * img_size[1]))

    # bbox-based head IoU — deprecated in Phase 34.2, 참고치로만 유지
    if head_r is None:
        head_iou, iou_fail = 0.0, True
    else:
        head_iou, iou_fail = _bbox_iou(head_o, head_r), False

    # silhouette IoU (Phase 34.2 주 구조 안정 지표)
    res_silhouette_mask = extract_foreground_mask(result_bytes)
    silh_iou_whole = silhouette_iou(orig_silhouette_mask, res_silhouette_mask)

    return {
        "mode": anchor.mode,
        "label_swapped": anchor.label_swapped,
        "position_gate_ok": anchor.position_gate_ok,
        "part_r2": r2,
        "face_mae": round(face_mae, 3),
        "edit_zone_change": round(edit_change, 3),
        "silhouette_iou_whole": round(silh_iou_whole, 4),
        "head_shape_iou": round(head_iou, 4),
        "head_iou_detect_fail": iou_fail,
        "hard_preserve_area_ratio": round(preserve_ratio, 5),
    }


async def _one_sample(
    pipeline: str, breed_id: str, style_id: str, image_url: str,
    orig_bytes: bytes, img_size: tuple[int, int],
    orig_silhouette_mask: np.ndarray,
) -> dict:
    started = datetime.now().isoformat(timespec="seconds")
    logger.info("[%s/%s/%s] 실행 시작", pipeline, breed_id, style_id)
    try:
        if pipeline == "anchor":
            result_url = await run_anchor_inpaint_pipeline(image_url, breed_id, style_id)
        else:
            result_url = await run_gemini_pipeline(image_url, breed_id, style_id)
    except QualityFailure as exc:
        logger.warning("[%s/%s/%s] QualityFailure: %s", pipeline, breed_id, style_id, exc)
        return {
            "ts": started, "pipeline": pipeline, "breed_id": breed_id, "style_id": style_id,
            "error": "QualityFailure", "reason": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s/%s/%s] RuntimeError: %s", pipeline, breed_id, style_id, exc)
        return {
            "ts": started, "pipeline": pipeline, "breed_id": breed_id, "style_id": style_id,
            "error": type(exc).__name__, "reason": str(exc),
        }

    result_bytes = await _download(result_url)
    metrics = await _measure(
        orig_bytes, result_bytes, image_url, img_size, orig_silhouette_mask
    )
    return {
        "ts": started,
        "pipeline": pipeline,
        "breed_id": breed_id,
        "style_id": style_id,
        "result_url": result_url,
        **metrics,
    }


def _summarize(rows: list[dict]) -> dict:
    def _stats(values: list[float]) -> dict:
        if not values:
            return {"n": 0}
        return {
            "n": len(values),
            "mean": round(statistics.mean(values), 3),
            "median": round(statistics.median(values), 3),
            "p25": round(float(np.percentile(values, 25)), 3),
            "p75": round(float(np.percentile(values, 75)), 3),
            "min": round(min(values), 3),
            "max": round(max(values), 3),
        }

    anchor_ok = [r for r in rows if r.get("pipeline") == "anchor" and "error" not in r]
    gemini_ok = [r for r in rows if r.get("pipeline") == "gemini" and "error" not in r]

    anchor_modes: dict[str, int] = {}
    for r in anchor_ok:
        anchor_modes[r["mode"]] = anchor_modes.get(r["mode"], 0) + 1

    summary = {
        "anchor": {
            "n_total": sum(1 for r in rows if r.get("pipeline") == "anchor"),
            "n_ok": len(anchor_ok),
            "mode_counts": anchor_modes,
            "face_mae": _stats([r["face_mae"] for r in anchor_ok]),
            "edit_zone_change": _stats([r["edit_zone_change"] for r in anchor_ok]),
            "silhouette_iou_whole": _stats([r["silhouette_iou_whole"] for r in anchor_ok]),
            "head_shape_iou_deprecated": _stats([r["head_shape_iou"] for r in anchor_ok]),
            "hard_preserve_area_ratio": _stats([r["hard_preserve_area_ratio"] for r in anchor_ok]),
            "head_iou_detect_fail_count": sum(1 for r in anchor_ok if r["head_iou_detect_fail"]),
            "silhouette_lt_0p70_count": sum(
                1 for r in anchor_ok if r["silhouette_iou_whole"] < 0.70
            ),
        },
        "gemini_baseline": {
            "n_total": sum(1 for r in rows if r.get("pipeline") == "gemini"),
            "n_ok": len(gemini_ok),
            "face_mae": _stats([r["face_mae"] for r in gemini_ok]),
            "edit_zone_change": _stats([r["edit_zone_change"] for r in gemini_ok]),
            "silhouette_iou_whole": _stats([r["silhouette_iou_whole"] for r in gemini_ok]),
            "head_shape_iou_deprecated": _stats([r["head_shape_iou"] for r in gemini_ok]),
            "hard_preserve_area_ratio": _stats([r["hard_preserve_area_ratio"] for r in gemini_ok]),
        },
        "edit_change_baseline_p25_candidate": _stats(
            [r["edit_zone_change"] for r in gemini_ok]
        ).get("p25"),
    }
    return summary


async def main_async() -> None:
    for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "GOOGLE_API_KEY", "FAL_KEY"):
        if not os.getenv(k):
            raise SystemExit(f"환경변수 미설정: {k}")
    if not IMAGE_PATH.exists():
        raise SystemExit(f"이미지 없음: {IMAGE_PATH}")
    _configure_cloudinary()

    orig_bytes = IMAGE_PATH.read_bytes()
    with Image.open(io.BytesIO(orig_bytes)) as img:
        img_size = img.size
    image_url = _upload(orig_bytes, "grooming-style/probe/anchor-inpaint-batch")
    logger.info("원본 업로드: %s (%s)", image_url, img_size)

    # silhouette IoU 기준 마스크 — 원본은 프로세스당 1회만 추출, 샘플 간 재사용
    logger.info("원본 silhouette 마스크 추출 (rembg u2net, 1회)")
    orig_silhouette_mask = extract_foreground_mask(orig_bytes)
    logger.info(
        "원본 silhouette 면적 비율: %.4f",
        float(orig_silhouette_mask.sum() / orig_silhouette_mask.size),
    )

    # 대표 스타일 = 각 견종의 styles[0]
    all_breeds = get_all_breeds()
    anchor_specs: list[tuple[str, str, str]] = [
        ("anchor", b["id"], b["styles"][0]["id"]) for b in all_breeds
    ]
    gemini_specs: list[tuple[str, str, str]] = [
        ("gemini", b["id"], b["styles"][0]["id"])
        for b in all_breeds if b["id"] in BASELINE_BREEDS
    ]
    specs = anchor_specs + gemini_specs
    logger.info("총 샘플: anchor=%d, gemini=%d", len(anchor_specs), len(gemini_specs))

    rows: list[dict] = []
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 이전 실행 잔존 방지 — 덮어쓰기
    OUTPUT_PATH.write_text("")

    for idx, (pipeline, breed_id, style_id) in enumerate(specs, start=1):
        logger.info("=== [%d/%d] ===", idx, len(specs))
        row = await _one_sample(
            pipeline, breed_id, style_id, image_url,
            orig_bytes, img_size, orig_silhouette_mask,
        )
        rows.append(row)
        with OUTPUT_PATH.open("a") as fp:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = _summarize(rows)
    print(json.dumps({"output": str(OUTPUT_PATH), "summary": summary, "rows": rows}, ensure_ascii=False))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
