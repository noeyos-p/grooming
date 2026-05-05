"""
EVF-SAM2 text-grounded anchor geometry 모듈.

존재 이유:
 - Phase 32~33 에서 Gemini VLM 이 동일 입력에서도 의미 수준의 regime-switching 을
   일으켜 얼굴 파트 bbox 가 재현성 없이 흔들린다는 점이 확인됐다.
 - 그 geometry source 를 `fal-ai/evf-sam` (text-grounded segmentation) 으로 대체하여
   결정론적으로 left_eye / right_eye / nose 앵커 마스크를 얻는다.

파이프라인:
 1) 3 개 파트(viewer-label) 를 병렬 호출
    - "left eye of the dog"   → viewer_left_eye
    - "right eye of the dog"  → viewer_right_eye
    - "nose of the dog"       → nose
 2) 각 마스크 PNG 를 원본 해상도로 불러와 bool 배열로 변환
 3) 눈: largest connected component 만 유지 (opening/fill/hull 없음)
 4) 코: largest_cc → opening(5x5) → fill_holes → (opt) convex_hull
 5) Label alignment:
    - EVF-SAM2 가 viewer-perspective 로 라벨링하는 경우가 있으므로
      최종 라벨은 cx 로 재판정한다.
    - 기존 `gemini_pipeline.py:174` 컨벤션(강아지 관점):
        left_eye  = 이미지 우측 = 더 큰 cx
        right_eye = 이미지 좌측 = 더 작은 cx
 6) Position gate (코):
    - nose.cy > mean(eyes.cy)
    - abs(nose.cx - mean(eyes.cx)) < 0.15 * image_w
 7) 최소 승인:
    - nose 없음 또는 눈 0 개 → QualityFailure
    - 눈 1 개만 → mode = "degraded_single_eye"
    - 눈 2 개 → mode = "full"
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from typing import Literal

import cv2
import fal_client
import httpx
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


# ── 상수 ──────────────────────────────────────────────────────────────────────

_PART_PROMPTS: dict[str, str] = {
    "viewer_left_eye": "left eye of the dog",
    "viewer_right_eye": "right eye of the dog",
    "nose": "nose of the dog",
}

# 코 cleanup 파라미터
_NOSE_OPENING_KERNEL = 5    # 3~5 권장, 5 가 상한
_NOSE_MORPH_SHAPE = cv2.MORPH_ELLIPSE

# 위치 게이트
_NOSE_CX_GATE_RATIO = 0.15  # abs(nose.cx - mean(eye.cx)) < ratio * image_w

# fal-ai/evf-sam 호출 인자
_EVF_SAM_MODEL = "fal-ai/evf-sam"
_EVF_SAM_ARGS_COMMON = {
    "semantic_type": True,
    "mask_only": True,
    "fill_holes": True,
}


# ── 예외 ──────────────────────────────────────────────────────────────────────


class QualityFailure(Exception):
    """앵커 최소 승인 실패 — 상위 파이프라인에서 422 로 매핑, Gemini 폴백 금지."""


# ── 데이터 구조 ──────────────────────────────────────────────────────────────


@dataclass
class PartMask:
    name: str
    mask: np.ndarray   # bool (H, W) — 원본 좌표계
    cx: float
    cy: float
    area_px: int
    bbox: tuple[int, int, int, int]  # (xmin, ymin, xmax, ymax)


@dataclass
class AnchorResult:
    image_size: tuple[int, int]                  # (W, H)
    parts: dict[str, PartMask]                   # keys: "left_eye" / "right_eye" / "nose"
    mode: Literal["full", "degraded_single_eye"]
    position_gate_ok: bool
    label_swapped: bool                           # viewer → dog 라벨이 뒤바뀌었는지
    diagnostics: dict = field(default_factory=dict)


# ── 마스크 I/O & 후처리 ──────────────────────────────────────────────────────


def _bool_mask_from_png(
    mask_bytes: bytes, target_size: tuple[int, int]
) -> np.ndarray:
    """mask PNG 를 target_size (W, H) 기준 bool 배열로 변환."""
    img = Image.open(io.BytesIO(mask_bytes)).convert("L")
    if img.size != target_size:
        img = img.resize(target_size, Image.NEAREST)
    return np.asarray(img, dtype=np.uint8) >= 128


def _to_u8(mask_bool: np.ndarray) -> np.ndarray:
    return (mask_bool.astype(np.uint8)) * 255


def _largest_cc(mask_bool: np.ndarray) -> np.ndarray:
    """8-연결성 largest connected component 만 유지."""
    if not mask_bool.any():
        return mask_bool.copy()
    u8 = _to_u8(mask_bool)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(u8, connectivity=8)
    if num <= 1:
        return mask_bool.copy()
    areas = stats[1:, cv2.CC_STAT_AREA]  # label 0 == background
    if areas.size == 0:
        return mask_bool.copy()
    keep_label = int(np.argmax(areas)) + 1
    return labels == keep_label


def _opening(mask_bool: np.ndarray, ksize: int) -> np.ndarray:
    u8 = _to_u8(mask_bool)
    kernel = cv2.getStructuringElement(_NOSE_MORPH_SHAPE, (ksize, ksize))
    opened = cv2.morphologyEx(u8, cv2.MORPH_OPEN, kernel)
    return opened >= 128


def _fill_holes(mask_bool: np.ndarray) -> np.ndarray:
    """floodfill 기반 내부 홀 채우기. (0, 0) 이 배경 영역이라고 가정."""
    u8 = _to_u8(mask_bool)
    h, w = u8.shape
    flood = u8.copy()
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(flood, ff_mask, (0, 0), 255)
    holes = cv2.bitwise_not(flood)  # 원 마스크 내부 중 floodfill 미도달 픽셀 == 홀
    filled = cv2.bitwise_or(u8, holes)
    return filled >= 128


def _convex_hull(mask_bool: np.ndarray) -> np.ndarray:
    if not mask_bool.any():
        return mask_bool.copy()
    u8 = _to_u8(mask_bool)
    contours, _ = cv2.findContours(u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return mask_bool.copy()
    all_pts = np.concatenate(contours, axis=0)
    hull = cv2.convexHull(all_pts)
    out = np.zeros_like(u8)
    cv2.drawContours(out, [hull], -1, 255, thickness=-1)
    return out >= 128


def _cleanup_nose(
    mask_bool: np.ndarray, use_convex_hull: bool
) -> tuple[np.ndarray, dict]:
    """nose 전용 cleanup 파이프라인 — 사용자 지정 순서대로 적용.

    순서:
      1) largest connected component
      2) opening (kernel 5x5)
      3) fill_holes
      4) optional convex hull
    """
    diag: dict = {
        "steps": ["largest_cc", "opening", "fill_holes"],
        "convex_hull_applied": bool(use_convex_hull),
        "opening_kernel": _NOSE_OPENING_KERNEL,
        "area_px": {"before": int(mask_bool.sum())},
    }
    m = _largest_cc(mask_bool)
    diag["area_px"]["after_lcc"] = int(m.sum())
    m = _opening(m, _NOSE_OPENING_KERNEL)
    diag["area_px"]["after_opening"] = int(m.sum())
    m = _fill_holes(m)
    diag["area_px"]["after_fill_holes"] = int(m.sum())
    if use_convex_hull:
        diag["steps"].append("convex_hull")
        m = _convex_hull(m)
        diag["area_px"]["after_convex_hull"] = int(m.sum())
    diag["area_px"]["final"] = int(m.sum())
    return m, diag


def _centroid_and_bbox(
    mask_bool: np.ndarray,
) -> tuple[float, float, int, tuple[int, int, int, int]] | None:
    ys, xs = np.nonzero(mask_bool)
    if xs.size == 0:
        return None
    return (
        float(xs.mean()),
        float(ys.mean()),
        int(xs.size),
        (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
    )


def _as_part(name: str, mask: np.ndarray) -> PartMask:
    stats = _centroid_and_bbox(mask)
    if stats is None:
        raise QualityFailure(f"{name} mask cleanup 후 foreground 0")
    cx, cy, area, bbox = stats
    return PartMask(name=name, mask=mask, cx=cx, cy=cy, area_px=area, bbox=bbox)


# ── fal-ai/evf-sam 호출 ───────────────────────────────────────────────────────


async def _download_png(url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=60.0)
        resp.raise_for_status()
        return resp.content


async def _call_evf_sam(
    image_url: str, prompt: str, target_size: tuple[int, int], tag: str
) -> tuple[np.ndarray, str] | None:
    """1 회 evf-sam 호출 → (mask bool, mask_url) 반환. 실패 시 None."""
    try:
        result = await asyncio.to_thread(
            fal_client.run,
            _EVF_SAM_MODEL,
            arguments={**_EVF_SAM_ARGS_COMMON, "prompt": prompt, "image_url": image_url},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[evf_sam_geometry] %s 호출 실패: %s", tag, exc)
        return None

    image_obj = result.get("image") if isinstance(result, dict) else None
    if not image_obj or "url" not in image_obj:
        logger.warning("[evf_sam_geometry] %s 응답에 image.url 없음: %s", tag, result)
        return None
    mask_url: str = image_obj["url"]

    try:
        mask_bytes = await _download_png(mask_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[evf_sam_geometry] %s mask 다운로드 실패: %s", tag, exc)
        return None

    mask_bool = _bool_mask_from_png(mask_bytes, target_size)
    return mask_bool, mask_url


# ── 핵심 API ──────────────────────────────────────────────────────────────────


async def get_anchor_masks(
    image_url: str,
    image_size: tuple[int, int],
    *,
    use_convex_hull: bool = False,
) -> AnchorResult:
    """EVF-SAM2 로부터 left_eye / right_eye / nose 앵커 마스크를 얻어 반환한다.

    Args:
        image_url: fal API 에 전달할 원본 이미지 public URL.
        image_size: 원본 해상도 (W, H). 마스크를 이 좌표계로 맞춰 반환.
        use_convex_hull: nose cleanup 마지막 단계에 convex hull 적용 여부 (기본 False).

    Raises:
        QualityFailure: nose 미검출 / 눈 0 개 / cleanup 후 foreground 0.

    주의:
      - position_gate_ok 가 False 여도 본 함수는 raise 하지 않는다.
        상위 파이프라인에서 명시적으로 판정·분기한다.
    """
    img_w, img_h = image_size
    logger.info(
        "[evf_sam_geometry] 앵커 요청 — image_size=%dx%d, hull=%s",
        img_w, img_h, use_convex_hull,
    )

    viewer_left_raw, viewer_right_raw, nose_raw = await asyncio.gather(
        _call_evf_sam(image_url, _PART_PROMPTS["viewer_left_eye"], image_size, "viewer_left_eye"),
        _call_evf_sam(image_url, _PART_PROMPTS["viewer_right_eye"], image_size, "viewer_right_eye"),
        _call_evf_sam(image_url, _PART_PROMPTS["nose"], image_size, "nose"),
    )

    diagnostics: dict = {
        "raw_mask_urls": {
            "viewer_left_eye": viewer_left_raw[1] if viewer_left_raw else None,
            "viewer_right_eye": viewer_right_raw[1] if viewer_right_raw else None,
            "nose": nose_raw[1] if nose_raw else None,
        }
    }

    # ── nose 처리 ──
    if nose_raw is None:
        raise QualityFailure("nose mask 탐지 실패")
    nose_raw_mask = nose_raw[0]
    if not nose_raw_mask.any():
        raise QualityFailure("nose mask foreground 0")

    nose_clean, nose_cleanup_diag = _cleanup_nose(nose_raw_mask, use_convex_hull)
    diagnostics["nose_cleanup"] = nose_cleanup_diag
    nose_part = _as_part("nose", nose_clean)

    # ── 눈 처리 (LCC 만) ──
    eyes_raw: list[tuple[str, np.ndarray]] = []
    for viewer_label, raw in (
        ("viewer_left_eye", viewer_left_raw),
        ("viewer_right_eye", viewer_right_raw),
    ):
        if raw is None:
            continue
        mask_bool = raw[0]
        if not mask_bool.any():
            logger.warning("[evf_sam_geometry] %s foreground 0 — 스킵", viewer_label)
            continue
        eyes_raw.append((viewer_label, _largest_cc(mask_bool)))

    if not eyes_raw:
        raise QualityFailure("눈 마스크 탐지 0/2")

    # ── label alignment (dog-perspective) ──
    #   cx 오름차순: [smaller_cx, larger_cx]
    #   smaller_cx (이미지 좌측) → 강아지 오른쪽 → right_eye
    #   larger_cx  (이미지 우측) → 강아지 왼쪽  → left_eye
    eye_entries: list[tuple[str, np.ndarray, float]] = []
    for viewer_label, mask in eyes_raw:
        stats = _centroid_and_bbox(mask)
        if stats is None:
            continue
        eye_entries.append((viewer_label, mask, stats[0]))
    eye_entries.sort(key=lambda t: t[2])

    parts: dict[str, PartMask] = {"nose": nose_part}
    if len(eye_entries) >= 2:
        smaller = eye_entries[0]
        larger = eye_entries[-1]
        parts["right_eye"] = _as_part("right_eye", smaller[1])
        parts["left_eye"] = _as_part("left_eye", larger[1])
        mode: Literal["full", "degraded_single_eye"] = "full"
        label_swapped = (
            smaller[0] != "viewer_right_eye" or larger[0] != "viewer_left_eye"
        )
        diagnostics["eye_label_mapping"] = {
            "smaller_cx_viewer_label": smaller[0],
            "smaller_cx_dog_label": "right_eye",
            "larger_cx_viewer_label": larger[0],
            "larger_cx_dog_label": "left_eye",
        }
    else:
        # degraded — 단일 눈 (viewer 라벨을 그대로 dog 좌우로 뒤집어 가정)
        only_viewer, only_mask, only_cx = eye_entries[0]
        dog_label = {
            "viewer_left_eye": "right_eye",
            "viewer_right_eye": "left_eye",
        }[only_viewer]
        parts[dog_label] = _as_part(dog_label, only_mask)
        mode = "degraded_single_eye"
        label_swapped = True
        diagnostics["eye_label_mapping"] = {
            "only_viewer_label": only_viewer,
            "only_cx": only_cx,
            "assumed_dog_label": dog_label,
        }
        logger.warning(
            "[evf_sam_geometry] degraded mode — 단일 눈만 검출 (%s → %s)",
            only_viewer, dog_label,
        )

    # ── 위치 게이트 ──
    eye_cxs = [p.cx for k, p in parts.items() if k in ("left_eye", "right_eye")]
    eye_cys = [p.cy for k, p in parts.items() if k in ("left_eye", "right_eye")]
    mean_eye_cx = float(np.mean(eye_cxs))
    mean_eye_cy = float(np.mean(eye_cys))
    nose_below_eyes = bool(nose_part.cy > mean_eye_cy)
    horiz_offset = abs(nose_part.cx - mean_eye_cx)
    nose_horiz_ok = bool(horiz_offset < _NOSE_CX_GATE_RATIO * img_w)
    position_gate_ok = bool(nose_below_eyes and nose_horiz_ok)
    diagnostics["position_gate"] = {
        "nose_cy": nose_part.cy,
        "mean_eye_cy": mean_eye_cy,
        "nose_below_eyes": nose_below_eyes,
        "nose_cx": nose_part.cx,
        "mean_eye_cx": mean_eye_cx,
        "horiz_offset_px": horiz_offset,
        "horiz_offset_ratio": horiz_offset / img_w if img_w > 0 else None,
        "horiz_gate_limit_ratio": _NOSE_CX_GATE_RATIO,
        "nose_horiz_ok": nose_horiz_ok,
        "overall_ok": position_gate_ok,
    }
    if not position_gate_ok:
        logger.warning(
            "[evf_sam_geometry] 위치 게이트 실패 — below=%s horiz_ok=%s (offset=%.1fpx / limit=%.1fpx)",
            nose_below_eyes,
            nose_horiz_ok,
            horiz_offset,
            _NOSE_CX_GATE_RATIO * img_w,
        )

    return AnchorResult(
        image_size=image_size,
        parts=parts,
        mode=mode,
        position_gate_ok=position_gate_ok,
        label_swapped=label_swapped,
        diagnostics=diagnostics,
    )
