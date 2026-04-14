"""
Vertex AI Imagen 3 Style Tuning 모델을 사용한 그루밍 스타일 변환 파이프라인.

모든 Vertex AI 추론 호출은 반드시 이 모듈을 통해서만 이루어진다.
"""

import asyncio
import io
import logging
import os

import cloudinary
import cloudinary.uploader
import httpx
import vertexai
from PIL import Image
from vertexai.preview.vision_models import ImageGenerationModel
from vertexai.preview.vision_models import Image as VertexImage

from services import style_prompts
from services.vertex_imagen_training import get_imagen_entry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 헬퍼 — MIME 타입 감지 (gemini_pipeline.py와 동일한 로직)
# ---------------------------------------------------------------------------

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
    # HEIC/HEIF: ftyp box (bytes 4-8 = "ftyp", bytes 8-12 = "heic"/"heix" 등)
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
            logger.info("[vertex_imagen_pipeline] HEIC → JPEG 변환 완료")
            return buf.getvalue(), True
        except Exception as exc:
            logger.warning(
                "[vertex_imagen_pipeline] HEIC → JPEG 변환 실패: %s — 원본 bytes 사용", exc
            )
    return image_bytes, False


# ---------------------------------------------------------------------------
# 추론 실행
# ---------------------------------------------------------------------------

def _run_inference(endpoint_id: str, image_bytes: bytes, prompt: str) -> bytes:
    """Vertex AI Imagen 3 튜닝 모델로 이미지를 변환하고 결과 bytes를 반환한다.

    동기 함수 — asyncio.to_thread()로 래핑해서 호출한다.

    Args:
        endpoint_id: 튜닝된 모델의 Vertex AI endpoint 리소스명
        image_bytes: 원본 이미지 raw bytes
        prompt: 스타일 변환 프롬프트

    Returns:
        변환된 이미지 bytes

    Raises:
        RuntimeError: Vertex AI API 호출 실패 또는 이미지 미반환
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    vertexai.init(project=project, location=location)

    # 튜닝된 엔드포인트로부터 모델 로드
    tuned_model = ImageGenerationModel.from_pretrained(endpoint_id)

    source_image = VertexImage(image_bytes=image_bytes)

    response = tuned_model.edit_image(
        base_image=source_image,
        prompt=prompt,
        number_of_images=1,
    )

    if not response.images:
        raise RuntimeError("Vertex AI Imagen API가 이미지를 반환하지 않았습니다.")

    result_image = response.images[0]
    # VertexImage → bytes 변환
    buf = io.BytesIO()
    result_image._pil_image.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 파이프라인 진입점
# ---------------------------------------------------------------------------

async def run_vertex_imagen_pipeline(image_url: str, breed_id: str, style_id: str) -> str:
    """
    Vertex AI Imagen 3 튜닝 모델을 사용해 강아지 사진을 그루밍 스타일로 변환한다.

    파이프라인 순서:
      1. style_prompts.get_prompt()로 프롬프트 조회
      2. imagen_registry에서 endpoint_id 조회 (status != "ready"면 ValueError)
      3. base64 dataURL → Cloudinary public URL 변환
      4. 이미지 bytes 다운로드 (httpx)
      5. HEIC → JPEG 변환 필요 시 처리
      6. Vertex AI Imagen 3 튜닝 모델 inference (asyncio.to_thread 래핑)
      7. 결과 bytes → Cloudinary 업로드 (folder="grooming-results") → URL 반환

    Args:
        image_url: 원본 강아지 이미지 URL (public URL 또는 data: 스킴)
        breed_id: 견종 ID (style_prompts.py 기준)
        style_id: 스타일 ID (해당 견종의 스타일)

    Returns:
        Cloudinary에 저장된 변환 결과 이미지 URL

    Raises:
        ValueError: 유효하지 않은 breed_id/style_id 또는 튜닝 모델 미준비
        RuntimeError: Vertex AI API 또는 Cloudinary 오류
    """
    # 1. 프롬프트 조회 — style_prompts.py가 유일한 데이터 소스
    prompt_data = style_prompts.get_prompt(breed_id, style_id)
    if prompt_data is None:
        raise ValueError(f"존재하지 않는 breed_id 또는 style_id: {breed_id}/{style_id}")

    prompt = prompt_data["prompt"]

    # 2. 레지스트리에서 endpoint_id 조회
    imagen_entry = get_imagen_entry(breed_id, style_id)
    if imagen_entry is None or imagen_entry.get("status") != "ready":
        raise ValueError(
            f"'{breed_id}/{style_id}'의 Imagen 튜닝 모델이 준비되지 않았습니다. "
            f"현재 상태: {imagen_entry.get('status') if imagen_entry else 'not found'}"
        )

    endpoint_id: str = imagen_entry["endpoint_id"]
    if not endpoint_id:
        raise ValueError(
            f"'{breed_id}/{style_id}'의 endpoint_id가 레지스트리에 없습니다."
        )

    logger.info(
        "[vertex_imagen_pipeline] 파이프라인 시작 — breed=%s, style=%s, endpoint=%s",
        breed_id,
        style_id,
        endpoint_id,
    )

    try:
        # 3. base64 dataURL 감지 → Cloudinary public URL로 교체
        if image_url.startswith("data:"):
            logger.info("[vertex_imagen_pipeline] base64 dataURL → Cloudinary 업로드")
            upload_result = cloudinary.uploader.upload(
                image_url,
                folder="grooming-style/uploads",
                overwrite=True,
                resource_type="image",
                format="jpg",
            )
            image_url = upload_result["secure_url"]
            logger.info("[vertex_imagen_pipeline] Cloudinary 업로드 완료: %s", image_url)

        # 4. 이미지 bytes 다운로드
        logger.info("[vertex_imagen_pipeline] 이미지 다운로드: %s", image_url)
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url)
            response.raise_for_status()
            image_bytes = response.content
        logger.info(
            "[vertex_imagen_pipeline] 이미지 다운로드 완료 (%d bytes)", len(image_bytes)
        )

        # 5. HEIC → JPEG 변환
        image_bytes, was_converted = _convert_to_jpeg_if_needed(image_bytes)
        if was_converted:
            logger.info("[vertex_imagen_pipeline] HEIC 변환 후 Cloudinary 재업로드")
            upload_result = cloudinary.uploader.upload(
                image_bytes,
                folder="grooming-style/uploads",
                overwrite=True,
                resource_type="image",
                format="jpg",
            )
            image_url = upload_result["secure_url"]
            logger.info(
                "[vertex_imagen_pipeline] 변환 이미지 Cloudinary 업로드 완료: %s", image_url
            )

        # 6. Vertex AI Imagen 3 튜닝 모델 inference
        logger.info("[vertex_imagen_pipeline] Vertex AI 추론 시작")
        result_bytes: bytes = await asyncio.to_thread(
            _run_inference, endpoint_id, image_bytes, prompt
        )
        logger.info(
            "[vertex_imagen_pipeline] Vertex AI 추론 완료 (%d bytes)", len(result_bytes)
        )

        # 7. Cloudinary 업로드
        logger.info("[vertex_imagen_pipeline] Cloudinary 업로드 시작")
        upload_result = cloudinary.uploader.upload(
            result_bytes,
            folder="grooming-results",
            resource_type="image",
        )
        result_url: str = upload_result["secure_url"]
        logger.info("[vertex_imagen_pipeline] Cloudinary 업로드 완료: %s", result_url)

        return result_url

    except ValueError:
        raise
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error(
            "[vertex_imagen_pipeline] 파이프라인 실패 (breed=%s, style=%s): %s",
            breed_id,
            style_id,
            exc,
            exc_info=True,
        )
        raise RuntimeError(
            f"Vertex AI Imagen 파이프라인 처리 중 오류가 발생했습니다: {exc}"
        ) from exc
