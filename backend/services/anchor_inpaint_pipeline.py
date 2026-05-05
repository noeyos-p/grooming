"""
Anchor-Inpainting v1.0 — EVF-SAM2 결정론적 앵커 기반 FLUX.1-fill 파이프라인.

설계 요약:
  - geometry source 는 `evf_sam_geometry.get_anchor_masks()` 가 전담한다
    (Gemini VLM 의 regime-switching 제거, Phase 32~33 참조).
  - 앵커(left_eye / right_eye / nose)의 bbox 사각형을 hard preserve 로,
    전신 실루엣(rembg/u2net) ∩ ¬앵커 dilate 영역을 편집 영역으로 지정해
    FLUX.1-fill 을 호출한다. (Phase 34.3 — head_bbox 한정 폐기)
  - 결과는 원본 해상도로 리사이즈한 뒤, 동일 좌표계 boolean index 치환으로
    원본 앵커 픽셀을 복원한다. (Phase 14 affine 재발 방지)

v1.0 범위 (plan 문서 준수):
  - 앵커 형태  : bbox 사각형 (contour 는 v1.1)
  - 앵커 파트  : left_eye / right_eye / nose (mouth 제외 — Phase 30)
  - edit 범위  : 전신 실루엣 ∩ ¬dilate(anchor_bbox, r2) — Phase 34.3
  - soft band  : 미적용 (Phase 32 Probe 불통 — 2-layer binary 고정)
  - 폴백       : QualityFailure 는 Gemini 로 내려가지 않는다 (ghost 경로 부활 금지)

금지 규칙 (CLAUDE.md·Phase 14·18-21·28·30 계승):
  - affine/좌표 정렬 합성 금지
  - pyramid blending · feather 합성 금지
  - 후처리 color LUT 금지 (색상 보존은 프롬프트 ABSOLUTE COLOR RULE 에만)
  - mouth 기본 보존 금지
"""

from __future__ import annotations

import asyncio
import io
import logging

import cloudinary
import cloudinary.uploader
import httpx
import numpy as np
from PIL import Image

from services.evf_sam_geometry import (
    PartMask,
    QualityFailure,
    get_anchor_masks,
)
from services.gemini_pipeline import _extract_dominant_fur_colors
from services.image_utils import _convert_to_jpeg_if_needed
from services.inpaint_pipeline import (
    _resize_for_flux,
    _resize_mask_for_flux,
    _run_flux_fill,
)
from services.silhouette_iou import extract_foreground_mask
from services.style_prompts import get_prompt

logger = logging.getLogger(__name__)

# r2 = hard preserve bbox 에서 편집 영역까지의 이격 — part 최소변 × coef × scale
_R2_MIN_PX = 8
_R2_MAX_PX = 28
_R2_COEF = 0.30
_DEGRADED_R2_SCALE = 1.2   # degraded_single_eye 모드에서 편집 영역을 앵커에서 더 떨어뜨림


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────


def _per_part_r2(parts: dict[str, PartMask], r2_scale: float) -> dict[str, int]:
    """파트별 r2(픽셀) 계산 — bbox 최소변의 0.30 × scale, clamp [8, 28].

    v1.0 은 soft band r1 을 사용하지 않는다 (Phase 32 Probe 불통).
    """
    out: dict[str, int] = {}
    for name, part in parts.items():
        xmin, ymin, xmax, ymax = part.bbox
        min_side = max(1, min(xmax - xmin + 1, ymax - ymin + 1))
        r2 = max(_R2_MIN_PX, min(_R2_MAX_PX, round(min_side * _R2_COEF * r2_scale)))
        out[name] = int(r2)
    return out


def _build_bbox_anchor_mask(
    parts: dict[str, PartMask], img_size: tuple[int, int]
) -> np.ndarray:
    """앵커 bbox 사각형 합집합을 uint8(H, W) 로 반환 — 255=hard preserve, 0=그 외."""
    w, h = img_size
    out = np.zeros((h, w), dtype=np.uint8)
    for part in parts.values():
        xmin, ymin, xmax, ymax = part.bbox
        x0 = max(0, xmin)
        y0 = max(0, ymin)
        x1 = min(w - 1, xmax)
        y1 = min(h - 1, ymax)
        if x1 < x0 or y1 < y0:
            continue
        out[y0 : y1 + 1, x0 : x1 + 1] = 255
    return out


def _build_two_layer_mask(
    parts: dict[str, PartMask],
    body_silhouette: np.ndarray,
    img_size: tuple[int, int],
    part_r2: dict[str, int],
) -> np.ndarray:
    """FLUX 마스크 (2-layer binary).

      - 전신 실루엣 ∩ ¬dilate(anchor_bbox, r2) = 255 (편집)
      - 그 외 (실루엣 바깥 OR 앵커 dilate 영역) = 0 (보존)
    """
    w, h = img_size
    flux = np.zeros((h, w), dtype=np.uint8)

    if body_silhouette.shape != (h, w):
        sil_img = Image.fromarray(
            (body_silhouette.astype(np.uint8) * 255), mode="L"
        ).resize((w, h), Image.NEAREST)
        body_silhouette = np.asarray(sil_img) >= 128
    flux[body_silhouette] = 255

    for name, part in parts.items():
        r2 = part_r2.get(name, _R2_MIN_PX)
        xmin, ymin, xmax, ymax = part.bbox
        dx0 = max(0, xmin - r2)
        dy0 = max(0, ymin - r2)
        dx1 = min(w, xmax + 1 + r2)
        dy1 = min(h, ymax + 1 + r2)
        if dx1 > dx0 and dy1 > dy0:
            flux[dy0:dy1, dx0:dx1] = 0

    return flux


def _numpy_l_to_png_bytes(arr: np.ndarray) -> bytes:
    img = Image.fromarray(arr, mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _hard_restore_anchors(
    original_bytes: bytes,
    flux_result_bytes: bytes,
    hard_preserve_arr: np.ndarray,
) -> bytes:
    """동일 좌표계 boolean index 치환 — affine/feather 금지 (Phase 14·28).

    1) FLUX 결과를 원본 해상도로 리사이즈
    2) hard_preserve_arr >= 128 위치에 원본 픽셀 복사
    """
    orig = Image.open(io.BytesIO(original_bytes)).convert("RGB")
    result = Image.open(io.BytesIO(flux_result_bytes)).convert("RGB")

    if result.size != orig.size:
        result = result.resize(orig.size, Image.LANCZOS)

    orig_arr = np.asarray(orig, dtype=np.uint8)
    result_arr = np.asarray(result, dtype=np.uint8).copy()

    mask_bool = hard_preserve_arr >= 128
    result_arr[mask_bool] = orig_arr[mask_bool]

    out = Image.fromarray(result_arr, mode="RGB")
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _compose_flux_prompt(base_prompt: str, extracted_colors: str) -> str:
    """ABSOLUTE COLOR RULE 을 base_prompt 앞에 주입 (inpaint_pipeline 과 동일 문구)."""
    if extracted_colors:
        color_clause = (
            f"The dog's fur color is exactly {extracted_colors}. "
            "Preserve this EXACT fur color. Do NOT change color under any circumstances."
        )
    else:
        color_clause = (
            "Preserve the dog's exact original fur color. "
            "Do NOT change the fur color."
        )
    return f"{color_clause} {base_prompt}"


# ── 메인 파이프라인 ──────────────────────────────────────────────────────────


async def run_anchor_inpaint_pipeline(
    image_url: str, breed_id: str, style_id: str
) -> str:
    """Anchor-Inpainting v1.0 entry point.

    Raises:
        ValueError:       breed_id / style_id 불일치 (상위에서 422 매핑).
        QualityFailure:   앵커 최소 승인 실패 또는 position gate 실패.
                          상위 라우터는 Gemini 로 폴백하지 말고 422 로 종료해야 한다.
        RuntimeError:     FLUX/네트워크/Cloudinary 등 인프라 실패.
                          상위 라우터에서 Gemini 경로로 폴백 허용.
    """
    prompt_data = get_prompt(breed_id, style_id)
    if prompt_data is None:
        raise ValueError(f"존재하지 않는 breed_id 또는 style_id: {breed_id}/{style_id}")
    base_prompt: str = prompt_data["prompt"]
    negative_prompt: str = prompt_data.get("negative_prompt", "") or ""

    logger.info("[anchor_inpaint] 시작 — breed=%s, style=%s", breed_id, style_id)

    try:
        # 1) base64 dataURL → Cloudinary
        if image_url.startswith("data:"):
            upload_result = cloudinary.uploader.upload(
                image_url,
                folder="grooming-style/uploads",
                resource_type="image",
                format="jpg",
            )
            image_url = upload_result["secure_url"]
            logger.info("[anchor_inpaint] dataURL 업로드 완료: %s", image_url)

        # 2) 이미지 bytes 다운로드
        async with httpx.AsyncClient() as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            image_bytes = resp.content

        # 3) HEIC → JPEG 변환 (필요 시 재업로드)
        image_bytes, was_converted = _convert_to_jpeg_if_needed(image_bytes)
        if was_converted:
            upload_result = cloudinary.uploader.upload(
                image_bytes,
                folder="grooming-style/uploads",
                resource_type="image",
                format="jpg",
            )
            image_url = upload_result["secure_url"]
            logger.info("[anchor_inpaint] HEIC 변환 재업로드: %s", image_url)

        orig_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img_w, img_h = orig_img.size
        logger.info("[anchor_inpaint] 원본 크기: %dx%d", img_w, img_h)

        # 4) 병렬 탐지 — body silhouette / anchor masks / 색상
        body_silhouette, anchor_result, extracted_colors = await asyncio.gather(
            asyncio.to_thread(extract_foreground_mask, image_bytes),
            get_anchor_masks(image_url, (img_w, img_h)),
            asyncio.to_thread(_extract_dominant_fur_colors, image_bytes),
        )

        # 5) position gate — 실패 시 QualityFailure (Gemini 폴백 금지)
        if not anchor_result.position_gate_ok:
            pg = anchor_result.diagnostics.get("position_gate", {})
            raise QualityFailure(
                "position_gate 실패 — "
                f"nose_below_eyes={pg.get('nose_below_eyes')}, "
                f"nose_horiz_ok={pg.get('nose_horiz_ok')}"
            )

        # 6) r2 산정 + hard preserve + FLUX 마스크
        r2_scale = (
            _DEGRADED_R2_SCALE
            if anchor_result.mode == "degraded_single_eye"
            else 1.0
        )
        part_r2 = _per_part_r2(anchor_result.parts, r2_scale)
        logger.info(
            "[anchor_inpaint] mode=%s, label_swapped=%s, part_r2=%s",
            anchor_result.mode, anchor_result.label_swapped, part_r2,
        )

        hard_preserve_arr = _build_bbox_anchor_mask(
            anchor_result.parts, (img_w, img_h)
        )
        flux_mask_arr = _build_two_layer_mask(
            anchor_result.parts, body_silhouette, (img_w, img_h), part_r2
        )
        flux_mask_png = _numpy_l_to_png_bytes(flux_mask_arr)

        # 7) FLUX 전송용 resize (inpaint_pipeline 재사용)
        flux_image_bytes = _resize_for_flux(image_bytes)
        flux_img_size = Image.open(io.BytesIO(flux_image_bytes)).size
        flux_mask_bytes = _resize_mask_for_flux(flux_mask_png, flux_img_size)
        logger.info(
            "[anchor_inpaint] FLUX 전송 크기: %s (원본: %dx%d)",
            flux_img_size, img_w, img_h,
        )

        # 8) FLUX 입력 Cloudinary 업로드
        flux_img_upload = cloudinary.uploader.upload(
            flux_image_bytes,
            folder="grooming-temp",
            resource_type="image",
            format="jpg",
        )
        flux_mask_upload = cloudinary.uploader.upload(
            flux_mask_bytes,
            folder="grooming-temp",
            resource_type="image",
            format="png",
        )
        flux_image_url = flux_img_upload["secure_url"]
        flux_mask_url = flux_mask_upload["secure_url"]

        # 9) FLUX.1-fill 호출
        flux_prompt = _compose_flux_prompt(base_prompt, extracted_colors)
        result_bytes = await _run_flux_fill(
            flux_image_url, flux_mask_url, flux_prompt, negative_prompt
        )

        # 10) 원본 좌표계 hard restore — boolean index 치환만 사용
        restored_bytes = _hard_restore_anchors(
            image_bytes, result_bytes, hard_preserve_arr
        )

        # 11) 결과 업로드
        upload = cloudinary.uploader.upload(
            restored_bytes,
            folder="grooming-results",
            resource_type="image",
        )
        result_url: str = upload["secure_url"]
        logger.info("[anchor_inpaint] 완료 — url=%s", result_url)
        return result_url

    except (ValueError, QualityFailure):
        raise
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("[anchor_inpaint] 예기치 않은 오류: %s", exc, exc_info=True)
        raise RuntimeError(f"Anchor-inpaint 처리 중 오류: {exc}") from exc
