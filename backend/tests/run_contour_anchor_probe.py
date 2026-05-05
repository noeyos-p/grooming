"""
Contour Anchor Probe — Phase 34 v1.1 Step A.

bbox 사각형 anchor(v1.0)와 contour(실제 세그멘테이션 마스크) anchor(v1.1 후보)를
같은 이미지·조합으로 비교한다.

핵심 차이:
  v1.0 hard_preserve: bbox 사각형 합집합
  v1.1 hard_preserve: part.mask (EVF-SAM2 실제 윤곽) 합집합
  v1.1 FLUX preserve zone: bbox dilate 대신 contour dilate(cv2.dilate, ellipse kernel)

측정 지표:
  - hard_preserve_area_ratio: contour가 더 낮아야 함 (bbox 내부 배경 픽셀 제거 효과)
  - face_mae: 앵커 위치 MAE — contour·bbox 비슷해야 함 (같은 픽셀 보존)
  - edit_zone_change: 편집 자유도. contour에서 높아지면 긍정 신호
  - silhouette_iou_whole: 구조 안정. ≥ 0.70 유지 여부 확인

입력:
  - 원본: ~/Downloads/IMG_7641.jpg
  - 비교 기준 (bbox v1.0): /tmp/anchor_inpaint_batch.jsonl
    (없으면 비교 없이 contour 결과만 출력)

산출:
  - /tmp/contour_anchor_probe.jsonl  — 샘플별 contour 결과
  - /tmp/contour_vs_bbox.json        — 지표 비교 요약
  - stdout: 요약 JSON

사용:
  cd backend && source venv/bin/activate
  python tests/run_contour_anchor_probe.py

PROBE_BREEDS 환경변수로 대상 견종 변경 (쉼표 구분):
  PROBE_BREEDS=maltese,poodle python tests/run_contour_anchor_probe.py
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
import cv2
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
    _DEGRADED_R2_SCALE,
    _per_part_r2,
    _compose_flux_prompt,
    _hard_restore_anchors,
    _numpy_l_to_png_bytes,
)
from services.evf_sam_geometry import (  # noqa: E402
    AnchorResult,
    PartMask,
    QualityFailure,
    get_anchor_masks,
)
from services.gemini_pipeline import _extract_dominant_fur_colors  # noqa: E402
from services.image_utils import _convert_to_jpeg_if_needed  # noqa: E402
from services.inpaint_pipeline import (  # noqa: E402
    _detect_full_head_bbox,
    _resize_for_flux,
    _resize_mask_for_flux,
    _run_flux_fill,
)
from services.silhouette_iou import extract_foreground_mask, silhouette_iou  # noqa: E402
from services.style_prompts import get_all_breeds, get_prompt  # noqa: E402

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")
OUTPUT_JSONL = Path("/tmp/contour_anchor_probe.jsonl")
COMPARE_JSONL = Path("/tmp/anchor_inpaint_batch.jsonl")
COMPARE_OUT = Path("/tmp/contour_vs_bbox.json")

# 기본 대상 견종 — 환경변수로 override 가능
_DEFAULT_PROBE_BREEDS = [
    "maltese", "poodle", "bichon", "shih_tzu", "pomeranian",
    "yorkshire_terrier", "papillon", "spitz", "mini_bichon",
    "bedlington", "maltipoo",
]

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("contour_probe")


# ── contour anchor mask 빌더 (v1.1) ─────────────────────────────────────────


def _build_contour_anchor_mask(
    parts: dict[str, PartMask], img_size: tuple[int, int]
) -> np.ndarray:
    """Contour anchor hard_preserve — part.mask 합집합.

    v1.0(_build_bbox_anchor_mask)과 차이:
      bbox 사각형 대신 EVF-SAM2 실제 세그멘테이션 마스크를 사용한다.
      bbox 내부의 배경 픽셀(눈 주변 털 등)이 hard_preserve에서 제외된다.
    """
    w, h = img_size
    out = np.zeros((h, w), dtype=np.uint8)
    for part in parts.values():
        if part.mask is not None and part.mask.shape == (h, w):
            out[part.mask] = 255
    return out


def _build_two_layer_mask_contour(
    parts: dict[str, PartMask],
    head_bbox: dict,
    img_size: tuple[int, int],
    part_r2: dict[str, int],
) -> np.ndarray:
    """FLUX 마스크 (contour 기반 2-layer binary).

    v1.0과 차이:
      preserve zone 계산 시 bbox를 r2 픽셀 확장하는 대신,
      part.mask를 cv2.dilate(ellipse kernel, r2) 로 팽창시킨다.

    결과:
      head_bbox 내부 ∩ ¬dilate(contour, r2) = 255 (편집)
      그 외 = 0 (보존)
    """
    w, h = img_size
    flux = np.zeros((h, w), dtype=np.uint8)

    hxmin = max(0, head_bbox["xmin"])
    hymin = max(0, head_bbox["ymin"])
    hxmax = min(w, head_bbox["xmax"])
    hymax = min(h, head_bbox["ymax"])
    if hxmax > hxmin and hymax > hymin:
        flux[hymin:hymax, hxmin:hxmax] = 255

    for name, part in parts.items():
        r2 = part_r2.get(name, 8)
        if part.mask is None or part.mask.shape != (h, w):
            continue
        kernel_size = 2 * r2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        mask_u8 = (part.mask.astype(np.uint8)) * 255
        dilated = cv2.dilate(mask_u8, kernel)
        flux[dilated >= 128] = 0

    return flux


# ── 지표 계산 ────────────────────────────────────────────────────────────────


def _masked_mae(orig: np.ndarray, result: np.ndarray, mask_bool: np.ndarray) -> float:
    if not mask_bool.any():
        return 0.0
    return float(
        np.abs(orig.astype(np.int16) - result.astype(np.int16))[mask_bool].mean()
    )


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


# ── Cloudinary / 네트워크 ─────────────────────────────────────────────────────


def _configure_cloudinary() -> None:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )


def _upload(image_bytes: bytes, folder: str, fmt: str = "jpg") -> str:
    res = cloudinary.uploader.upload(
        image_bytes, folder=folder, resource_type="image", format=fmt
    )
    return res["secure_url"]


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=120.0)
        r.raise_for_status()
        return r.content


# ── 단건 파이프라인 (contour anchor) ────────────────────────────────────────


async def _run_contour_pipeline(
    image_url: str,
    image_bytes: bytes,
    breed_id: str,
    style_id: str,
    orig_silhouette_mask: np.ndarray,
    gemini_client,
) -> dict:
    """contour anchor 로 단건 실행 후 지표 반환."""
    prompt_data = get_prompt(breed_id, style_id)
    if prompt_data is None:
        raise ValueError(f"존재하지 않는 breed/style: {breed_id}/{style_id}")
    base_prompt: str = prompt_data["prompt"]
    negative_prompt: str = prompt_data.get("negative_prompt", "") or ""

    orig_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img_w, img_h = orig_img.size
    img_size = (img_w, img_h)

    # 병렬 탐지
    head_bbox, anchor_result, extracted_colors = await asyncio.gather(
        _detect_full_head_bbox(image_bytes, gemini_client),
        get_anchor_masks(image_url, img_size),
        asyncio.to_thread(_extract_dominant_fur_colors, image_bytes),
    )

    if head_bbox is None:
        raise RuntimeError("head_bbox 탐지 실패")
    if not anchor_result.position_gate_ok:
        pg = anchor_result.diagnostics.get("position_gate", {})
        raise QualityFailure(
            f"position_gate 실패 — "
            f"nose_below_eyes={pg.get('nose_below_eyes')}, "
            f"nose_horiz_ok={pg.get('nose_horiz_ok')}"
        )

    r2_scale = (
        _DEGRADED_R2_SCALE if anchor_result.mode == "degraded_single_eye" else 1.0
    )
    part_r2 = _per_part_r2(anchor_result.parts, r2_scale)
    logger.info(
        "[contour_probe] mode=%s, part_r2=%s", anchor_result.mode, part_r2
    )

    # contour 마스크 빌드
    hard_preserve_arr = _build_contour_anchor_mask(anchor_result.parts, img_size)
    flux_mask_arr = _build_two_layer_mask_contour(
        anchor_result.parts, head_bbox, img_size, part_r2
    )
    flux_mask_png = _numpy_l_to_png_bytes(flux_mask_arr)

    # FLUX resize + 업로드
    flux_image_bytes = _resize_for_flux(image_bytes)
    flux_img_size = Image.open(io.BytesIO(flux_image_bytes)).size
    flux_mask_bytes = _resize_mask_for_flux(flux_mask_png, flux_img_size)

    flux_img_url = _upload(flux_image_bytes, "grooming-temp", "jpg")
    flux_mask_url = _upload(flux_mask_bytes, "grooming-temp", "png")

    flux_prompt = _compose_flux_prompt(base_prompt, extracted_colors)
    result_bytes = await _run_flux_fill(flux_img_url, flux_mask_url, flux_prompt, negative_prompt)

    restored_bytes = _hard_restore_anchors(image_bytes, result_bytes, hard_preserve_arr)

    result_url = _upload(restored_bytes, "grooming-results/contour-probe")

    # 지표 측정
    orig_arr = np.asarray(orig_img, dtype=np.uint8)
    res_img = Image.open(io.BytesIO(restored_bytes)).convert("RGB")
    if res_img.size != orig_img.size:
        res_img = res_img.resize(orig_img.size, Image.LANCZOS)
    res_arr = np.asarray(res_img, dtype=np.uint8)

    hard_bool = hard_preserve_arr >= 128
    edit_bool = flux_mask_arr >= 128

    face_mae = _masked_mae(orig_arr, res_arr, hard_bool)
    edit_change = _masked_mae(orig_arr, res_arr, edit_bool)
    preserve_ratio = float(hard_bool.sum() / (img_w * img_h))

    res_silhouette_mask = extract_foreground_mask(restored_bytes)
    silh_iou = silhouette_iou(orig_silhouette_mask, res_silhouette_mask)

    head_r = await _detect_full_head_bbox(restored_bytes, gemini_client)
    if head_r is not None:
        head_iou = _bbox_iou(head_bbox, head_r)
        head_iou_fail = False
    else:
        head_iou, head_iou_fail = 0.0, True

    # bbox anchor 대비 contour 면적 비교 (참고용)
    contour_area_px = int(hard_bool.sum())
    bbox_area_px = 0
    for part in anchor_result.parts.values():
        xmin, ymin, xmax, ymax = part.bbox
        x0, y0 = max(0, xmin), max(0, ymin)
        x1, y1 = min(img_w - 1, xmax), min(img_h - 1, ymax)
        if x1 >= x0 and y1 >= y0:
            bbox_area_px += (x1 - x0 + 1) * (y1 - y0 + 1)
    contour_vs_bbox_ratio = (
        round(contour_area_px / bbox_area_px, 4) if bbox_area_px > 0 else None
    )

    return {
        "mask_type": "contour",
        "mode": anchor_result.mode,
        "label_swapped": anchor_result.label_swapped,
        "position_gate_ok": anchor_result.position_gate_ok,
        "part_r2": part_r2,
        "result_url": result_url,
        "face_mae": round(face_mae, 3),
        "edit_zone_change": round(edit_change, 3),
        "silhouette_iou_whole": round(silh_iou, 4),
        "head_shape_iou": round(head_iou, 4),
        "head_iou_detect_fail": head_iou_fail,
        "hard_preserve_area_ratio": round(preserve_ratio, 5),
        "contour_area_px": contour_area_px,
        "bbox_area_px": bbox_area_px,
        "contour_vs_bbox_ratio": contour_vs_bbox_ratio,
    }


# ── 요약 ─────────────────────────────────────────────────────────────────────


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


def _load_bbox_baseline(breeds: list[str]) -> dict[str, dict]:
    """기존 bbox v1.0 결과에서 breed별 첫 anchor 행 반환."""
    if not COMPARE_JSONL.exists():
        return {}
    baseline: dict[str, dict] = {}
    for line in COMPARE_JSONL.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("pipeline") != "anchor" or "error" in row:
            continue
        bid = row.get("breed_id", "")
        if bid in breeds and bid not in baseline:
            baseline[bid] = row
    return baseline


# ── main ─────────────────────────────────────────────────────────────────────


async def main_async() -> None:
    for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET",
              "GOOGLE_API_KEY", "FAL_KEY"):
        if not os.getenv(k):
            raise SystemExit(f"환경변수 미설정: {k}")
    if not IMAGE_PATH.exists():
        raise SystemExit(f"이미지 없음: {IMAGE_PATH}")
    _configure_cloudinary()

    probe_breeds_env = os.getenv("PROBE_BREEDS", "")
    if probe_breeds_env:
        probe_breeds = [b.strip() for b in probe_breeds_env.split(",") if b.strip()]
    else:
        probe_breeds = _DEFAULT_PROBE_BREEDS

    # breed_id → styles[0] 매핑
    all_breeds = get_all_breeds()
    breed_style_map: dict[str, str] = {
        b["id"]: b["styles"][0]["id"] for b in all_breeds
    }
    specs = [
        (bid, breed_style_map[bid])
        for bid in probe_breeds
        if bid in breed_style_map
    ]
    if not specs:
        raise SystemExit("probe 대상 breed 없음")
    logger.info("contour anchor probe 대상: %d개 — %s", len(specs), [s[0] for s in specs])

    orig_bytes = IMAGE_PATH.read_bytes()
    with Image.open(io.BytesIO(orig_bytes)) as img:
        img_size_log = img.size
    image_url = cloudinary.uploader.upload(
        orig_bytes,
        folder="grooming-style/probe/contour-anchor",
        resource_type="image",
        format="jpg",
    )["secure_url"]
    logger.info("원본 업로드: %s (%s)", image_url, img_size_log)

    logger.info("원본 silhouette 마스크 추출 (1회)")
    orig_silhouette_mask = extract_foreground_mask(orig_bytes)

    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSONL.write_text("")

    rows: list[dict] = []
    for idx, (breed_id, style_id) in enumerate(specs, start=1):
        started = datetime.now().isoformat(timespec="seconds")
        logger.info("=== [%d/%d] %s/%s ===", idx, len(specs), breed_id, style_id)
        try:
            metrics = await _run_contour_pipeline(
                image_url, orig_bytes, breed_id, style_id,
                orig_silhouette_mask, gemini_client,
            )
            row = {"ts": started, "breed_id": breed_id, "style_id": style_id, **metrics}
        except QualityFailure as exc:
            logger.warning("[%s/%s] QualityFailure: %s", breed_id, style_id, exc)
            row = {
                "ts": started, "breed_id": breed_id, "style_id": style_id,
                "error": "QualityFailure", "reason": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s/%s] 오류: %s", breed_id, style_id, exc)
            row = {
                "ts": started, "breed_id": breed_id, "style_id": style_id,
                "error": type(exc).__name__, "reason": str(exc),
            }
        rows.append(row)
        with OUTPUT_JSONL.open("a") as fp:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
        logger.info(
            "[%s/%s] 완료 — silh=%.4f, edit=%.2f, face_mae=%.3f, preserve_ratio=%.5f, contour_vs_bbox=%.4f",
            breed_id, style_id,
            row.get("silhouette_iou_whole", 0),
            row.get("edit_zone_change", 0),
            row.get("face_mae", 0),
            row.get("hard_preserve_area_ratio", 0),
            row.get("contour_vs_bbox_ratio") or 0,
        )

    ok_rows = [r for r in rows if "error" not in r]

    # contour 요약
    contour_summary = {
        "n_total": len(rows),
        "n_ok": len(ok_rows),
        "silhouette_iou_whole": _stats([r["silhouette_iou_whole"] for r in ok_rows]),
        "edit_zone_change": _stats([r["edit_zone_change"] for r in ok_rows]),
        "face_mae": _stats([r["face_mae"] for r in ok_rows]),
        "hard_preserve_area_ratio": _stats([r["hard_preserve_area_ratio"] for r in ok_rows]),
        "contour_vs_bbox_ratio": _stats(
            [r["contour_vs_bbox_ratio"] for r in ok_rows if r.get("contour_vs_bbox_ratio") is not None]
        ),
        "silhouette_lt_0p70_count": sum(1 for r in ok_rows if r["silhouette_iou_whole"] < 0.70),
        "edit_lt_baseline_p25_count": sum(
            1 for r in ok_rows if r["edit_zone_change"] < 20.155
        ),
    }

    # bbox v1.0 비교
    bbox_baseline = _load_bbox_baseline(probe_breeds)
    comparison: list[dict] = []
    for r in ok_rows:
        bid = r["breed_id"]
        b = bbox_baseline.get(bid)
        if b is None:
            continue
        comparison.append({
            "breed_id": bid,
            "style_id": r["style_id"],
            "contour_silh_iou": r["silhouette_iou_whole"],
            "bbox_silh_iou": b.get("silhouette_iou_whole"),
            "delta_silh_iou": round(r["silhouette_iou_whole"] - (b.get("silhouette_iou_whole") or 0), 4),
            "contour_edit_change": r["edit_zone_change"],
            "bbox_edit_change": b.get("edit_zone_change"),
            "delta_edit_change": round(r["edit_zone_change"] - (b.get("edit_zone_change") or 0), 3),
            "contour_face_mae": r["face_mae"],
            "bbox_face_mae": b.get("face_mae"),
            "delta_face_mae": round(r["face_mae"] - (b.get("face_mae") or 0), 3),
            "contour_preserve_ratio": r["hard_preserve_area_ratio"],
            "bbox_preserve_ratio": b.get("hard_preserve_area_ratio"),
            "contour_vs_bbox_ratio": r.get("contour_vs_bbox_ratio"),
        })

    compare_out = {
        "contour_summary": contour_summary,
        "bbox_vs_contour_per_breed": comparison,
    }
    COMPARE_OUT.write_text(json.dumps(compare_out, ensure_ascii=False, indent=2))
    logger.info("비교 결과 저장: %s", COMPARE_OUT)

    print(json.dumps({
        "output": str(OUTPUT_JSONL),
        "compare_output": str(COMPARE_OUT),
        "contour_summary": contour_summary,
        "comparison": comparison,
        "rows": rows,
    }, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
