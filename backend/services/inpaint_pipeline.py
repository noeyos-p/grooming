"""
fal.ai FLUX.1-fill 기반 inpainting 파이프라인.

마스크 전략:
  - features_bbox(눈/코/입 tight 영역, ~9%)만 검정(보존) → FLUX가 머리 털 포함 전체 스타일 변환
  - FLUX 실행 후 features_bbox 영역에 원본 픽셀 하드 합성 → 눈/코/입 100% 보존

FLUX 전처리:
  - 원본 이미지를 max 1024px로 리사이즈 후 전송 → FLUX 내부 크롭/비율왜곡 방지
  - 합성 단계에서 비율 좌표로 원본 해상도 복원

마스크 컨벤션:
  - 흰색(255) = 인페인팅 대상 (머리 털 + 몸통)
  - 검정(0)   = 보존 (눈/코/입 features 영역)
"""

import asyncio
import io
import json
import logging
import os
import re

import cloudinary
import cloudinary.uploader
import fal_client
import httpx
from google import genai
from google.genai.types import Part
from PIL import Image, ImageDraw, ImageFilter

from services.image_utils import _convert_to_jpeg_if_needed, _detect_mime_type
from services.gemini_pipeline import _extract_dominant_fur_colors
from services.style_prompts import get_prompt

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")

_MODEL_ANALYSIS = "gemini-2.5-flash"
_FLUX_MAX_DIM = 1024  # FLUX 전송 전 리사이즈 최대 변 (내부 크롭/비율왜곡 방지)


def _resize_for_flux(image_bytes: bytes, max_dim: int = _FLUX_MAX_DIM) -> bytes:
    """FLUX 전송용으로 이미지를 max_dim 이하로 리사이즈한다.

    - 가로·세로 비율 유지
    - 결과 크기는 8의 배수로 맞춤 (FLUX 권장)
    - 원본이 이미 max_dim 이하이면 그대로 반환

    Returns:
        리사이즈된 JPEG bytes (원본과 동일 크기면 원본 반환)
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) <= max_dim:
        return image_bytes

    ratio = max_dim / max(w, h)
    new_w = int(w * ratio)
    new_h = int(h * ratio)
    # 8의 배수로 내림
    new_w = max(8, new_w - (new_w % 8))
    new_h = max(8, new_h - (new_h % 8))

    resized = img.resize((new_w, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    resized.save(buf, format="JPEG", quality=95)
    logger.info("[inpaint_pipeline] FLUX 리사이즈: %dx%d → %dx%d", w, h, new_w, new_h)
    return buf.getvalue()


def _resize_mask_for_flux(mask_bytes: bytes, target_size: tuple[int, int]) -> bytes:
    """마스크 PNG를 target_size로 리사이즈한다 (NEAREST — 흑백 마스크 보존)."""
    img = Image.open(io.BytesIO(mask_bytes))
    if img.size == target_size:
        return mask_bytes
    resized = img.resize(target_size, Image.NEAREST)
    buf = io.BytesIO()
    resized.save(buf, format="PNG")
    return buf.getvalue()


async def _detect_full_head_bbox(image_bytes: bytes, gemini_client) -> dict | None:
    """강아지 머리 전체 bbox를 탐지한다.

    _detect_face_bbox(눈+코 영역)와 달리, 귀 끝부터 턱/혀 아래까지 포함한
    전체 머리 영역을 반환한다. 혀가 bbox 밖으로 빠져나가 인페인팅 구간에 포함되는
    문제를 방지하기 위해 사용한다.

    반환: {"xmin": px, "ymin": px, "xmax": px, "ymax": px} (픽셀 단위)
    실패 시 None 반환.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img_w, img_h = img.size

        mime_type = _detect_mime_type(image_bytes)
        image_part = Part.from_bytes(data=image_bytes, mime_type=mime_type)
        detection_prompt = (
            "Find the bounding box that covers the dog's ENTIRE HEAD in this image.\n"
            "The box must include: top of the skull, both ears, eyes, nose, mouth, "
            "tongue (if visible), and below the chin.\n"
            "Make sure the bottom edge is BELOW the tongue tip if the tongue is out.\n"
            'Return ONLY this JSON, no other text:\n'
            '{"xmin": 0-100, "ymin": 0-100, "xmax": 0-100, "ymax": 0-100}\n'
            "Values are percentages: 0=top-left corner, 100=bottom-right corner."
        )
        text_part = Part.from_text(text=detection_prompt)
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=_MODEL_ANALYSIS,
            contents=[image_part, text_part],
        )
        raw = response.text or ""
        json_match = re.search(r"\{[\s\S]*?\}", raw)
        if not json_match:
            logger.warning("[inpaint_pipeline] 머리 bbox 탐지: JSON 파싱 실패 — raw=%s", raw[:200])
            return None
        data = json.loads(json_match.group())
        required = {"xmin", "ymin", "xmax", "ymax"}
        if not required.issubset(data.keys()):
            logger.warning("[inpaint_pipeline] 머리 bbox 탐지: 필수 키 누락 — keys=%s", list(data.keys()))
            return None
        bbox = {
            "xmin": int(data["xmin"] / 100 * img_w),
            "ymin": int(data["ymin"] / 100 * img_h),
            "xmax": int(data["xmax"] / 100 * img_w),
            "ymax": int(data["ymax"] / 100 * img_h),
        }
        logger.info("[inpaint_pipeline] 머리 bbox 탐지 성공: %s", bbox)
        return bbox
    except Exception as exc:
        logger.warning("[inpaint_pipeline] 머리 bbox 탐지 실패: %s", exc)
        return None


def _generate_face_mask(image_bytes: bytes, face_bbox: dict, padding_ratio: float = 0.5) -> bytes:
    """얼굴 전체 bbox를 검정(보존)으로, 나머지를 흰색(인페인팅 대상)으로 하는 마스크를 생성한다.

    개별 눈/코/입 방식 대신 얼굴 전체를 하나의 보존 영역으로 처리하여
    FLUX.1-fill의 잔상(ghost) 아티팩트를 방지한다.

    근접 촬영 시 얼굴이 이미지 면적의 40% 이상을 차지하면 패딩을 자동 축소한다.
    고정 50% 패딩은 근접 촬영에서 마스크가 이미지 전체를 덮어 FLUX가 아무것도
    인페인팅하지 못하는 문제를 일으킨다.

    Args:
        image_bytes: 원본 이미지 raw bytes
        face_bbox: 픽셀 단위 얼굴 bbox {"xmin", "ymin", "xmax", "ymax"}
        padding_ratio: 얼굴 크기 대비 패딩 비율 (기본 50%; 근접 촬영 시 자동 축소)

    Returns:
        PNG 마스크 bytes
    """
    img = Image.open(io.BytesIO(image_bytes))
    img_w, img_h = img.size

    fw = face_bbox["xmax"] - face_bbox["xmin"]
    fh = face_bbox["ymax"] - face_bbox["ymin"]

    # 적응형 패딩 — bbox 면적 비율에 따라 자동 조정
    # features_bbox(~9%)처럼 작은 경우에도 과도한 패딩이 대부분을 덮지 않도록 함
    face_area_ratio = (fw * fh) / (img_w * img_h)
    if face_area_ratio > 0.40:
        effective_padding = 0.05   # 근접 촬영 전체 머리: 5%
    elif face_area_ratio > 0.20:
        effective_padding = 0.20   # 중간 크기: 20%
    elif face_area_ratio > 0.08:
        effective_padding = 0.12   # features bbox (8~20%): 12%
    else:
        effective_padding = 0.08   # 매우 작은 features: 8%
    logger.info(
        "[inpaint_pipeline] 마스크 패딩 — face_area_ratio=%.2f → effective_padding=%.2f",
        face_area_ratio, effective_padding,
    )
    pad = int(max(fw, fh) * effective_padding)

    padded_xmin = max(0, face_bbox["xmin"] - pad)
    padded_ymin = max(0, face_bbox["ymin"] - pad)
    padded_xmax = min(img_w, face_bbox["xmax"] + pad)
    padded_ymax = min(img_h, face_bbox["ymax"] + pad)

    # 전체 흰색 마스크 (인페인팅 대상)
    mask = Image.new("L", (img_w, img_h), 255)
    draw = ImageDraw.Draw(mask)
    # 직사각형으로 보존 — 타원(ellipse)은 하단 중앙(혀 위치)이 곡선으로 파여
    # 혀가 흰색 구간에 걸리는 문제가 있어 직사각형으로 변경. bbox 전체를 균일하게 보존.
    draw.rectangle([padded_xmin, padded_ymin, padded_xmax, padded_ymax], fill=0)

    # 경계 페더링 — blur_radius만큼 가장자리를 흐리게 처리
    blur_radius = max(20, int(max(fw, fh) * 0.06))
    feathered = mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # 패딩된 bbox 안쪽을 순수 검정으로 재보장 — grey zone을 바깥 가장자리로만 한정
    shrink = blur_radius
    draw2 = ImageDraw.Draw(feathered)
    draw2.rectangle(
        [
            padded_xmin + shrink,
            padded_ymin + shrink,
            padded_xmax - shrink,
            padded_ymax - shrink,
        ],
        fill=0,
    )

    buf = io.BytesIO()
    feathered.save(buf, format="PNG")
    logger.info(
        "[inpaint_pipeline] 얼굴 마스크 생성 완료 — 원본 bbox=%s, 패딩=%dpx, blur=%dpx",
        face_bbox,
        pad,
        blur_radius,
    )
    return buf.getvalue()


async def _detect_face_features_bbox(image_bytes: bytes, gemini_client) -> dict | None:
    """눈·코·입 영역만 커버하는 tight bbox를 탐지한다 (귀·두개골·턱 제외).

    _detect_full_head_bbox는 귀~혀 전체를 포함하므로 근접 촬영에서는
    이미지 대부분을 덮는다. 합성(compositing)에는 이 함수가 반환하는
    더 작은 영역이 필요하다.

    반환: {"xmin": px, "ymin": px, "xmax": px, "ymax": px} (픽셀 단위)
    실패 시 None 반환.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img_w, img_h = img.size

        mime_type = _detect_mime_type(image_bytes)
        image_part = Part.from_bytes(data=image_bytes, mime_type=mime_type)
        detection_prompt = (
            "Find the bounding box covering ONLY the dog's eyes, nose, and mouth.\n"
            "Do NOT include ears, top of head, cheeks, or chin below the mouth.\n"
            "The box should be a TIGHT rectangle around just the eye-nose-mouth triangle.\n"
            'Return ONLY this JSON, no other text:\n'
            '{"xmin": 0-100, "ymin": 0-100, "xmax": 0-100, "ymax": 0-100}\n'
            "Values are percentages of image size (0=top-left, 100=bottom-right)."
        )
        text_part = Part.from_text(text=detection_prompt)
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=_MODEL_ANALYSIS,
            contents=[image_part, text_part],
        )
        raw = response.text or ""
        json_match = re.search(r"\{[\s\S]*?\}", raw)
        if not json_match:
            logger.warning("[inpaint_pipeline] 얼굴 features bbox 탐지: JSON 파싱 실패 — raw=%s", raw[:200])
            return None
        data = json.loads(json_match.group())
        required = {"xmin", "ymin", "xmax", "ymax"}
        if not required.issubset(data.keys()):
            return None
        bbox = {
            "xmin": int(data["xmin"] / 100 * img_w),
            "ymin": int(data["ymin"] / 100 * img_h),
            "xmax": int(data["xmax"] / 100 * img_w),
            "ymax": int(data["ymax"] / 100 * img_h),
        }
        logger.info("[inpaint_pipeline] 얼굴 features bbox 탐지 성공: %s", bbox)
        return bbox
    except Exception as exc:
        logger.warning("[inpaint_pipeline] 얼굴 features bbox 탐지 실패: %s", exc)
        return None


def _composite_original_face(
    original_bytes: bytes,
    result_bytes: bytes,
    face_bbox: dict,
    padding_ratio: float = 0.2,
) -> bytes:
    """FLUX 결과 이미지 위에 원본 얼굴 픽셀을 직접 합성한다.

    - 비율 좌표(0.0~1.0)로 변환 후 각 이미지 크기에 매핑하므로
      FLUX 출력 해상도가 원본과 달라도 동일 위치에 정확히 합성된다.
    - 원본 얼굴 크롭을 FLUX 결과의 해당 영역 크기로 리사이즈 후 붙여넣기.
    - 경계 페더링(GaussianBlur)으로 자연스러운 전환.
    - 합성 실패 시 FLUX 결과를 원본 해상도로 리사이즈해 반환 (Gemini fallback 방지).

    Args:
        original_bytes: 원본 이미지 bytes
        result_bytes: FLUX 결과 이미지 bytes
        face_bbox: 픽셀 단위 얼굴 bbox {"xmin", "ymin", "xmax", "ymax"} (원본 좌표)
        padding_ratio: bbox 패딩 비율 (기본 20%)

    Returns:
        합성된 결과 이미지 bytes (JPEG)
    """
    try:
        original = Image.open(io.BytesIO(original_bytes)).convert("RGB")
        result = Image.open(io.BytesIO(result_bytes)).convert("RGB")

        orig_w, orig_h = original.size
        res_w, res_h = result.size

        fw = face_bbox["xmax"] - face_bbox["xmin"]
        fh = face_bbox["ymax"] - face_bbox["ymin"]
        pad = int(max(fw, fh) * padding_ratio)

        # 패딩 적용 bbox (원본 픽셀 좌표)
        ox1 = max(0, face_bbox["xmin"] - pad)
        oy1 = max(0, face_bbox["ymin"] - pad)
        ox2 = min(orig_w, face_bbox["xmax"] + pad)
        oy2 = min(orig_h, face_bbox["ymax"] + pad)

        # 비율 좌표 → FLUX 결과 픽셀 좌표로 매핑
        rx1 = int(ox1 / orig_w * res_w)
        ry1 = int(oy1 / orig_h * res_h)
        rx2 = int(ox2 / orig_w * res_w)
        ry2 = int(oy2 / orig_h * res_h)

        crop_w = rx2 - rx1
        crop_h = ry2 - ry1
        if crop_w <= 0 or crop_h <= 0:
            raise ValueError(f"얼굴 크롭 크기가 0 — rx=({rx1},{rx2}), ry=({ry1},{ry2})")

        # 원본 얼굴 크롭 → FLUX 결과 해당 영역 크기로 리사이즈
        face_crop = original.crop((ox1, oy1, ox2, oy2))
        face_crop_resized = face_crop.resize((crop_w, crop_h), Image.LANCZOS)

        # 경계 페더링 마스크 (크롭 좌표계 기준)
        blur_r = max(8, int(max(crop_w, crop_h) * 0.04))
        face_mask = Image.new("L", (crop_w, crop_h), 255)
        face_mask = face_mask.filter(ImageFilter.GaussianBlur(radius=blur_r))
        # 중심부 재고정 — 눈/코/입이 blur에 의해 흐려지지 않도록
        d = ImageDraw.Draw(face_mask)
        d.rectangle([blur_r, blur_r, crop_w - blur_r, crop_h - blur_r], fill=255)

        # FLUX 결과에 원본 얼굴 크롭 붙여넣기 (페더링 마스크 적용)
        out = result.copy()
        out.paste(face_crop_resized, (rx1, ry1), mask=face_mask)

        # 출력 해상도를 원본에 맞춤
        if out.size != (orig_w, orig_h):
            out = out.resize((orig_w, orig_h), Image.LANCZOS)

        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=95)
        logger.info(
            "[inpaint_pipeline] 원본 얼굴 합성 완료 — "
            "orig_crop=(%d,%d,%d,%d) result_paste=(%d,%d,%d,%d)",
            ox1, oy1, ox2, oy2, rx1, ry1, rx2, ry2,
        )
        return buf.getvalue()

    except Exception as exc:
        logger.warning(
            "[inpaint_pipeline] 얼굴 합성 실패 — FLUX 결과 원본 해상도로 반환: %s", exc
        )
        # 합성 실패 시 FLUX 결과를 원본 크기로만 맞춰 반환 (Gemini fallback 방지)
        try:
            orig_size = Image.open(io.BytesIO(original_bytes)).size
            res_img = Image.open(io.BytesIO(result_bytes)).convert("RGB")
            if res_img.size != orig_size:
                res_img = res_img.resize(orig_size, Image.LANCZOS)
            buf = io.BytesIO()
            res_img.save(buf, format="JPEG", quality=95)
            return buf.getvalue()
        except Exception:
            return result_bytes


async def _run_flux_fill(
    image_url: str, mask_url: str, prompt: str, negative_prompt: str = ""
) -> bytes:
    """fal.ai FLUX.1-fill pro로 inpainting을 실행하고 결과 bytes를 반환한다.

    Args:
        image_url: 원본 이미지 Cloudinary URL
        mask_url: 마스크 이미지 Cloudinary URL
        prompt: 스타일 변환 프롬프트 (색상 보존 지시 포함)
        negative_prompt: 네거티브 프롬프트 (선택)

    Returns:
        inpainting 결과 이미지 bytes

    Raises:
        RuntimeError: fal.ai API 오류 또는 다운로드 실패
    """
    logger.info("[inpaint_pipeline] FLUX.1-fill 호출 시작")

    arguments: dict = {
        "image_url": image_url,
        "mask_url": mask_url,
        "prompt": prompt,
        "output_format": "jpeg",
    }
    if negative_prompt:
        arguments["negative_prompt"] = negative_prompt

    result = await asyncio.to_thread(
        fal_client.run,
        "fal-ai/flux-pro/v1/fill",
        arguments=arguments,
    )
    output_url = result["images"][0]["url"]
    logger.info("[inpaint_pipeline] FLUX.1-fill 완료 — output_url=%s", output_url)

    async with httpx.AsyncClient() as client:
        resp = await client.get(output_url, timeout=60.0)
        resp.raise_for_status()
        return resp.content


async def run_inpaint_pipeline(image_url: str, breed_id: str, style_id: str) -> str:
    """fal.ai FLUX.1-fill 기반 inpainting 파이프라인.

    파이프라인 순서:
      1. 프롬프트 조회
      2. base64 dataURL이면 Cloudinary 업로드
      3. 이미지 bytes 다운로드
      4. HEIC → JPEG 변환
      5. Gemini로 머리 전체 bbox 탐지 (귀~혀/턱 포함)
      6. 머리 전체 보존 마스크 생성 (50% 패딩 포함 타원)
      7. 마스크 Cloudinary 업로드
      8. FLUX.1-fill inpainting 실행
      9. 결과 Cloudinary 업로드 후 URL 반환

    Args:
        image_url: 원본 강아지 이미지 URL (public URL 또는 data: 스킴)
        breed_id: 견종 ID
        style_id: 스타일 ID

    Returns:
        Cloudinary에 저장된 변환 결과 이미지 URL

    Raises:
        ValueError: 유효하지 않은 breed_id 또는 style_id
        RuntimeError: 파이프라인 내부 오류
    """
    # 1. 프롬프트 조회
    prompt_data = get_prompt(breed_id, style_id)
    if prompt_data is None:
        raise ValueError(f"존재하지 않는 breed_id 또는 style_id: {breed_id}/{style_id}")

    prompt = prompt_data["prompt"]
    logger.info(
        "[inpaint_pipeline] 파이프라인 시작 — breed=%s, style=%s", breed_id, style_id
    )

    try:
        # 2. base64 dataURL → Cloudinary public URL
        if image_url.startswith("data:"):
            logger.info("[inpaint_pipeline] base64 dataURL → Cloudinary 업로드")
            upload_result = cloudinary.uploader.upload(
                image_url,
                folder="grooming-style/uploads",
                resource_type="image",
                format="jpg",
            )
            image_url = upload_result["secure_url"]
            logger.info("[inpaint_pipeline] Cloudinary 업로드 완료: %s", image_url)

        # 3. 이미지 bytes 다운로드
        logger.info("[inpaint_pipeline] 이미지 다운로드: %s", image_url)
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url)
            response.raise_for_status()
            image_bytes = response.content
        logger.info("[inpaint_pipeline] 이미지 다운로드 완료 (%d bytes)", len(image_bytes))

        # 4. HEIC → JPEG 변환
        image_bytes, was_converted = _convert_to_jpeg_if_needed(image_bytes)
        if was_converted:
            logger.info("[inpaint_pipeline] HEIC 변환 후 Cloudinary 재업로드")
            upload_result = cloudinary.uploader.upload(
                image_bytes,
                folder="grooming-style/uploads",
                resource_type="image",
                format="jpg",
            )
            image_url = upload_result["secure_url"]
            logger.info("[inpaint_pipeline] 변환 이미지 Cloudinary 업로드 완료: %s", image_url)

        # 5. 털 색상 추출 + 머리 전체 bbox + 얼굴 features bbox (3개 병렬)
        #    - head_bbox  : FLUX 마스크용 (귀~혀 포함 전체 머리)
        #    - features_bbox: 합성(compositing)용 (눈/코/입 tight 영역만)
        gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
        extracted_colors, head_bbox, features_bbox = await asyncio.gather(
            asyncio.to_thread(_extract_dominant_fur_colors, image_bytes),
            _detect_full_head_bbox(image_bytes, gemini_client),
            _detect_face_features_bbox(image_bytes, gemini_client),
        )

        if head_bbox is None:
            raise RuntimeError("머리 bbox 탐지 실패 — Gemini 응답 없음 또는 파싱 오류")

        # features_bbox 탐지 실패 시 head_bbox를 축소해 fallback
        if features_bbox is None:
            logger.warning("[inpaint_pipeline] features bbox 탐지 실패 — head_bbox 중앙 40%% 사용")
            fw = head_bbox["xmax"] - head_bbox["xmin"]
            fh = head_bbox["ymax"] - head_bbox["ymin"]
            features_bbox = {
                "xmin": head_bbox["xmin"] + int(fw * 0.20),
                "ymin": head_bbox["ymin"] + int(fh * 0.15),
                "xmax": head_bbox["xmax"] - int(fw * 0.20),
                "ymax": head_bbox["ymax"] - int(fh * 0.15),
            }
            logger.info("[inpaint_pipeline] features bbox fallback: %s", features_bbox)

        # 편의상 기존 변수명 유지 (마스크 생성에 사용)
        face_bbox = head_bbox

        # 5-1. 색상 보존 지시를 FLUX 프롬프트 앞에 주입
        if extracted_colors:
            logger.info("[inpaint_pipeline] 추출된 털 색상: %s", extracted_colors)
            color_clause = (
                f"The dog's fur color is exactly {extracted_colors}. "
                "Preserve this EXACT fur color. Do NOT change color under any circumstances."
            )
        else:
            logger.warning("[inpaint_pipeline] 털 색상 추출 실패 — 일반 색상 보존 지시 사용")
            color_clause = "Preserve the dog's exact original fur color. Do NOT change the fur color."

        flux_prompt = f"{color_clause} {prompt}"
        logger.info("[inpaint_pipeline] FLUX 프롬프트 구성 완료")

        # 6. features_bbox 기반 마스크 생성 (눈/코/입 tight 영역만 보존)
        #    head_bbox 전체 보존 시 FLUX가 머리 털을 전혀 변환하지 못하는 문제 수정
        mask_bytes = _generate_face_mask(image_bytes, features_bbox)
        logger.info("[inpaint_pipeline] 마스크 생성 완료 (%d bytes)", len(mask_bytes))

        # 7. FLUX 전송 전 이미지·마스크를 max 1024px로 리사이즈
        #    원본 대형 이미지(예: 3024×4032)를 그대로 보내면 FLUX 내부 크롭으로 다리 등 하단이 잘림
        orig_img_for_size = Image.open(io.BytesIO(image_bytes))
        orig_w, orig_h = orig_img_for_size.size

        flux_image_bytes = _resize_for_flux(image_bytes)
        flux_img_size = Image.open(io.BytesIO(flux_image_bytes)).size
        flux_mask_bytes = _resize_mask_for_flux(mask_bytes, flux_img_size)
        logger.info(
            "[inpaint_pipeline] FLUX 전송 크기: %s (원본: %dx%d)", flux_img_size, orig_w, orig_h
        )

        # 7-1. 리사이즈된 이미지·마스크 Cloudinary 업로드
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
        logger.info("[inpaint_pipeline] FLUX용 이미지/마스크 업로드 완료")

        # 8. FLUX.1-fill inpainting
        result_bytes = await _run_flux_fill(
            flux_image_url,
            flux_mask_url,
            flux_prompt,
            negative_prompt=prompt_data.get("negative_prompt", ""),
        )
        flux_result_size = Image.open(io.BytesIO(result_bytes)).size
        logger.info(
            "[inpaint_pipeline] FLUX.1-fill 결과 수신 (%d bytes, 크기=%s)",
            len(result_bytes), flux_result_size,
        )

        # 8-1. 원본 눈/코/입 픽셀 하드 합성 (features_bbox 사용 — head_bbox보다 작은 tight 영역)
        result_bytes = _composite_original_face(image_bytes, result_bytes, features_bbox)
        logger.info("[inpaint_pipeline] 원본 얼굴 합성 완료 (%d bytes)", len(result_bytes))

        # 9. 결과 Cloudinary 업로드
        logger.info("[inpaint_pipeline] 결과 Cloudinary 업로드 시작")
        result_upload = cloudinary.uploader.upload(
            result_bytes,
            folder="grooming-results",
            resource_type="image",
        )
        result_url: str = result_upload["secure_url"]
        logger.info("[inpaint_pipeline] 결과 Cloudinary 업로드 완료: %s", result_url)

        return result_url

    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        logger.error(
            "[inpaint_pipeline] 파이프라인 실패 (breed=%s, style=%s): %s",
            breed_id,
            style_id,
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"Inpaint 파이프라인 처리 중 오류가 발생했습니다: {exc}") from exc
