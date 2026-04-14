"""
Google Gemini 2.0 Flash 기반 이미지 생성 파이프라인.
모든 Gemini API 호출은 반드시 이 모듈을 통해서만 이루어진다.

변환 전략:
  - Gemini: 전체 이미지 스타일 변환
  - 프롬프트 강화로 눈/코/입 보존 지시 (SAM 픽셀 합성 미사용)
"""

import pillow_heif
pillow_heif.register_heif_opener()

import asyncio
import io
import logging
import os

import cloudinary
import cloudinary.uploader
import httpx
from google import genai
from google.genai import types
from google.genai.types import GenerateContentConfig, Part
from PIL import Image

from services.style_prompts import get_prompt

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

_MODEL_GEMINI = "gemini-3.1-flash-image-preview"
_MODEL_ANALYSIS = "gemini-2.5-flash"


def _extract_dominant_fur_colors(image_bytes: bytes) -> str:
    """이미지 bytes에서 주요 털 색상을 추출해 프롬프트용 문자열로 반환한다.

    배경으로 추정되는 매우 밝은 색(RGB 각각 230 이상)과 매우 어두운 색(RGB 각각 30 이하)은
    제외하고, 남은 색상 중 상위 빈도 3~5가지의 RGB 값을 반환한다.

    Args:
        image_bytes: 원본 이미지 raw bytes

    Returns:
        프롬프트에 바로 삽입 가능한 색상 문자열.
        예: "rgb(180, 150, 120) and rgb(210, 180, 150)"
        색상 추출에 실패하면 빈 문자열을 반환한다.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = img.resize((100, 100), Image.LANCZOS)

        # getcolors는 (count, pixel) 튜플 리스트 반환
        colors = img.getcolors(maxcolors=100 * 100)
        if not colors:
            return ""

        # 배경 가능성이 높은 극단 색상 제거
        def _is_background(rgb: tuple) -> bool:
            r, g, b = rgb
            return (r >= 230 and g >= 230 and b >= 230) or (r <= 30 and g <= 30 and b <= 30)

        filtered = [(count, pixel) for count, pixel in colors if not _is_background(pixel)]
        if not filtered:
            return ""

        # 빈도 내림차순 정렬 후 상위 5가지 선택
        filtered.sort(key=lambda x: x[0], reverse=True)
        top_colors = [pixel for _, pixel in filtered[:5]]

        color_strings = [f"rgb({r}, {g}, {b})" for r, g, b in top_colors]
        return " and ".join(color_strings)

    except Exception as exc:
        logger.warning("[gemini_pipeline] 털 색상 추출 실패: %s", exc)
        return ""


def _detect_mime_type(image_bytes: bytes) -> str:
    """이미지 bytes의 magic number로 MIME 타입을 감지한다.

    HEIC, JPEG, PNG, WEBP를 지원하며, 알 수 없는 경우 image/jpeg를 반환한다.
    """
    if image_bytes[:4] == b"\x89PNG":
        return "image/png"
    if image_bytes[:3] in (b"\xff\xd8\xff",):
        return "image/jpeg"
    if image_bytes[:4] in (b"RIFF",) and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    # HEIC/HEIF: ftyp box (bytes 4-8 = "ftyp", bytes 8-12 = "heic"/"heix"/"hevc"/"mif1" 등)
    if len(image_bytes) >= 12 and image_bytes[4:8] == b"ftyp":
        return "image/heic"
    return "image/jpeg"


def _convert_to_jpeg_if_needed(image_bytes: bytes) -> tuple[bytes, bool]:
    """HEIC 등 비표준 포맷을 JPEG로 변환한다. JPEG/PNG는 그대로 반환.

    Returns:
        (변환 후 bytes, 변환 여부 bool)
    """
    mime = _detect_mime_type(image_bytes)
    if mime == "image/heic":
        try:
            img = Image.open(io.BytesIO(image_bytes))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=95)
            logger.info("[gemini_pipeline] HEIC → JPEG 변환 완료")
            return buf.getvalue(), True
        except Exception as exc:
            logger.warning("[gemini_pipeline] HEIC → JPEG 변환 실패: %s — 원본 bytes 사용", exc)
    return image_bytes, False


async def _analyze_dog_features(image_bytes: bytes) -> str:
    """
    gemini-2.5-flash (텍스트 모델)로 원본 강아지 이미지의 핵심 특징을 분석합니다.
    분석 결과는 이미지 변환 프롬프트에 주입되어 얼굴 보존 정확도를 높입니다.
    """
    try:
        gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
        mime_type = _detect_mime_type(image_bytes)
        image_part = Part.from_bytes(data=image_bytes, mime_type=mime_type)
        analysis_prompt = (
            "Analyze this dog photo and describe the following concisely in English:\n"
            "1. Eyes: color, shape, position\n"
            "2. Nose: color, shape\n"
            "3. Mouth/tongue: expression, tongue visible or not\n"
            "4. Pose: sitting/standing, paw position\n"
            "5. Fur color: main colors and pattern\n"
            "6. Background: brief description\n"
            "Be specific and concise. This will be used to preserve these features during grooming style transformation."
        )
        text_part = Part.from_text(text=analysis_prompt)
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=_MODEL_ANALYSIS,
            contents=[image_part, text_part],
        )
        analysis = response.text or ""
        logger.info(f"[gemini_pipeline] dog feature analysis completed ({len(analysis)} chars)")
        return analysis
    except Exception as e:
        logger.warning(f"[gemini_pipeline] feature analysis failed, skipping: {e}")
        return ""


async def _run_gemini(image_bytes: bytes, prompt: str) -> bytes:
    """Gemini로 이미지를 변환하고 결과 bytes를 반환한다.

    Args:
        image_bytes: 원본 이미지의 raw bytes
        prompt: 스타일 변환 프롬프트

    Returns:
        변환된 이미지 bytes

    Raises:
        RuntimeError: Gemini API가 이미지를 반환하지 않은 경우
    """
    logger.info("[gemini_pipeline] Gemini API 호출 시작 (model=%s)", _MODEL_GEMINI)
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

    mime_type = _detect_mime_type(image_bytes)
    logger.info("[gemini_pipeline] 감지된 MIME 타입: %s", mime_type)
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    text_part = prompt

    gemini_response = gemini_client.models.generate_content(
        model=_MODEL_GEMINI,
        contents=[image_part, text_part],
        config=GenerateContentConfig(
            response_modalities=["IMAGE"],
        ),
    )
    logger.info("[gemini_pipeline] Gemini API 응답 수신")

    candidates = gemini_response.candidates
    if not candidates:
        raise RuntimeError("Gemini API가 후보를 반환하지 않았습니다.")

    candidate = candidates[0]
    finish_reason = getattr(candidate, "finish_reason", None)
    logger.info("[gemini_pipeline] finish_reason: %s", finish_reason)

    if candidate.content is None:
        raise RuntimeError(
            f"Gemini API content가 None입니다 (finish_reason={finish_reason}). "
            "Safety filter 또는 이미지 변환 거부일 수 있습니다."
        )

    result_bytes: bytes | None = None
    for part in candidate.content.parts:
        if hasattr(part, "inline_data") and part.inline_data is not None:
            result_bytes = part.inline_data.data
            break

    if result_bytes is None:
        raise RuntimeError("Gemini API가 이미지를 반환하지 않았습니다.")

    logger.info("[gemini_pipeline] Gemini 이미지 추출 완료 (%d bytes)", len(result_bytes))
    return result_bytes


async def run_gemini_pipeline(image_url: str, breed_id: str, style_id: str) -> str:
    """
    Gemini 2.0 Flash를 사용해 강아지 사진을 그루밍 스타일로 변환한다.
    프롬프트 강화로 눈/코/입 보존을 Gemini에 지시한다 (SAM 픽셀 합성 미사용).

    파이프라인 순서:
      1. 프롬프트 베이스 조회
      2. base64 dataURL이면 Cloudinary public URL로 교체
      3. 이미지 bytes 다운로드
      4. HEIC이면 JPEG 변환 후 Cloudinary 재업로드
      5. PIL로 주요 털 색상 추출 후 강화된 프롬프트 구성
      6. Gemini API 호출 (이미지 + 강화된 프롬프트)
      7. Cloudinary 업로드 후 URL 반환

    Args:
        image_url: 원본 강아지 이미지 URL (public URL 또는 data: 스킴)
        breed_id: 견종 ID (style_prompts.py 기준)
        style_id: 스타일 ID (해당 견종의 스타일)

    Returns:
        Cloudinary에 저장된 변환 결과 이미지 URL

    Raises:
        ValueError: 유효하지 않은 breed_id 또는 style_id
        RuntimeError: Gemini API 또는 Cloudinary 오류
    """
    # 1. 프롬프트 조회 + 강화 — style_prompts.py가 유일한 데이터 소스
    prompt_data = get_prompt(breed_id, style_id)
    if prompt_data is None:
        raise ValueError(f"존재하지 않는 breed_id 또는 style_id: {breed_id}/{style_id}")

    base_prompt = prompt_data["prompt"]

    logger.info(
        "[gemini_pipeline] 파이프라인 시작 — breed=%s, style=%s", breed_id, style_id
    )

    try:
        # 2. base64 dataURL 감지 → Cloudinary public URL로 교체
        if image_url.startswith("data:"):
            logger.info("[gemini_pipeline] base64 dataURL → Cloudinary 업로드")
            upload_result = cloudinary.uploader.upload(
                image_url,
                folder="grooming-style/uploads",
                overwrite=True,
                resource_type="image",
                format="jpg",
            )
            image_url = upload_result["secure_url"]
            logger.info("[gemini_pipeline] Cloudinary 업로드 완료: %s", image_url)

        # 3. 이미지 bytes 다운로드 (Gemini API는 bytes 입력 필요)
        logger.info("[gemini_pipeline] 이미지 다운로드: %s", image_url)
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url)
            response.raise_for_status()
            image_bytes = response.content
        logger.info("[gemini_pipeline] 이미지 다운로드 완료 (%d bytes)", len(image_bytes))

        # 4. HEIC → JPEG 변환 (Gemini는 HEIC 미지원)
        image_bytes, was_converted = _convert_to_jpeg_if_needed(image_bytes)
        if was_converted:
            logger.info("[gemini_pipeline] HEIC 변환 후 Cloudinary 재업로드")
            upload_result = cloudinary.uploader.upload(
                image_bytes,
                folder="grooming-style/uploads",
                overwrite=True,
                resource_type="image",
                format="jpg",
            )
            image_url = upload_result["secure_url"]
            logger.info("[gemini_pipeline] 변환 이미지 Cloudinary 업로드 완료: %s", image_url)

        # 5. 특징 분석 + 털 색상 추출 후 프롬프트 구성

        # 5-1. 텍스트 모델로 강아지 특징 분석
        analysis = await _analyze_dog_features(image_bytes)

        # 5-2. 털 색상 추출
        extracted_colors = _extract_dominant_fur_colors(image_bytes)
        if extracted_colors:
            logger.info("[gemini_pipeline] 추출된 털 색상: %s", extracted_colors)
            color_clause = f"The dog's fur/coat color is extracted from the original photo as {extracted_colors}. Use EXACTLY these colors for the fur. Do NOT lighten, darken, or change the hue."
        else:
            logger.warning("[gemini_pipeline] 털 색상 추출 실패 — 일반 색상 보존 지시 사용")
            color_clause = "Preserve the dog's exact original fur/coat color — do NOT change any color."

        gemini_prompt = (
            f"{base_prompt}\n\n"
            "CRITICAL REQUIREMENTS:\n"
            f"1. EXACT FUR COLOR MATCH: {color_clause}\n"
            "2. FACIAL FEATURE PRESERVATION - POSITION AND PROPORTION: Preserve the exact spatial layout of the dog's face:\n"
            "   - Eyes: maintain exact inter-eye distance relative to face width, eye size relative to face, and vertical position\n"
            "   - Nose: maintain exact position (horizontal center, vertical ratio from top of head to chin)\n"
            "   - Mouth: maintain exact position relative to nose and chin\n"
            "   - Even if you cannot copy the exact pixel appearance, the geometric proportions and positions must be identical to the original photo.\n"
            "3. UNCHANGED ELEMENTS: Eyes shape/color, nose shape/color, mouth expression — keep these as close to original as possible.\n"
            "4. CHANGE ONLY: fur cut style, fur length, fur texture, and grooming shape.\n"
            "5. The overall dog identity, face proportions, and expression must match the original photo exactly."
        )

        # 5-3. 분석 결과를 프롬프트에 주입
        if analysis:
            enhanced_prompt = (
                f"{gemini_prompt}\n\n"
                f"ORIGINAL DOG FEATURES TO PRESERVE EXACTLY:\n{analysis}\n\n"
                f"RULE: Preserve ALL features listed above pixel-perfectly. Change ONLY the fur cut style and length."
            )
        else:
            enhanced_prompt = gemini_prompt

        # 6. Gemini API 호출 (강화된 프롬프트로 눈/코/입 보존 및 털 색상 지시)
        result_bytes = await _run_gemini(image_bytes, enhanced_prompt)

        # 7. Cloudinary 업로드
        logger.info("[gemini_pipeline] Cloudinary 업로드 시작")
        upload_result = cloudinary.uploader.upload(
            result_bytes,
            folder="grooming-results",
            resource_type="image",
        )
        result_url: str = upload_result["secure_url"]
        logger.info("[gemini_pipeline] Cloudinary 업로드 완료: %s", result_url)

        return result_url

    except ValueError:
        raise
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error(
            "[gemini_pipeline] 파이프라인 실패 (breed=%s, style=%s): %s",
            breed_id,
            style_id,
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"Gemini 파이프라인 처리 중 오류가 발생했습니다: {exc}") from exc
