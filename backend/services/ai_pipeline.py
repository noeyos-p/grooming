"""
Replicate API 오케스트레이션 — AI 파이프라인 단일 진입점.
모든 Replicate API 호출은 반드시 이 모듈을 통해서만 이루어진다.

파이프라인 순서 (LoRA 전용):
  업로드
  → LoRA img2img 추론 (학습된 LoRA 필수)
  → Cloudinary 저장 → URL 반환

LoRA가 학습되지 않은 breed+style 조합은 ValueError로 거부한다.
ControlNet / SAM / PIL compositing 경로는 완전히 제거됐다.
"""

import asyncio
import logging
import os

import cloudinary
import cloudinary.uploader
import replicate
from replicate.exceptions import ReplicateError

from services import lora_training
from services.style_prompts import get_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cloudinary 초기화 (환경변수 기반)
# ---------------------------------------------------------------------------
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.environ.get("CLOUDINARY_API_KEY", ""),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET", ""),
)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _upload_base64_to_cloudinary(data_url: str) -> str:
    """base64 dataURL을 Cloudinary에 업로드하고 공개 URL을 반환한다.

    Replicate API는 public URL만 허용하므로, 프론트엔드가 전달한
    data:image/...;base64,... 형식을 먼저 Cloudinary public URL로 변환한다.
    public_id를 지정하지 않아 Cloudinary가 unique ID를 자동 생성한다.
    """
    logger.info("[pipeline] base64 dataURL → Cloudinary 업로드 시작")
    upload_result = cloudinary.uploader.upload(
        data_url,
        folder="grooming-style/uploads",
        overwrite=True,
        resource_type="image",
        format="jpg",  # HEIC 등 비표준 형식을 JPEG로 변환 (Replicate는 HEIC 미지원)
    )
    public_url: str = upload_result["secure_url"]
    logger.info("[pipeline] base64 업로드 완료: %s", public_url)
    return public_url


async def _upload_url_to_cloudinary(image_url: str, breed_id: str, style_id: str) -> str:
    """URL로부터 Cloudinary에 업로드하고 공개 URL을 반환한다."""
    logger.info("[pipeline] Cloudinary 업로드 시작 (url)")
    upload_result = cloudinary.uploader.upload(
        image_url,
        folder="grooming-style",
        public_id=f"{breed_id}_{style_id}",
        overwrite=True,
        invalidate=True,  # CDN 엣지 캐시 즉시 무효화 — 동일 breed+style 재실행 시 구버전 서빙 방지
        resource_type="image",
    )
    result_url: str = upload_result["secure_url"]
    logger.info("[pipeline] Cloudinary 업로드 완료: %s", result_url)
    return result_url


async def _run_with_lora(
    image_url: str,
    model_ref: str,
    prompt: str,
    negative_prompt: str,
) -> str:
    """학습된 LoRA 모델로 img2img 스타일 변환. 3회 재시도 포함.

    Args:
        image_url:       원본 강아지 이미지 public URL
        model_ref:       "{owner}/{model}:{version}" 형식의 Replicate 모델 참조
        prompt:          trigger word가 포함된 스타일 프롬프트
        negative_prompt: 네거티브 프롬프트

    Returns:
        Replicate 출력 이미지 URL
    """
    logger.info("[pipeline] LoRA 추론 경로 시작: %s", model_ref)
    for attempt in range(3):
        try:
            output = await replicate.async_run(
                model_ref,
                input={
                    "image": image_url,
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "strength": 0.75,
                    "num_inference_steps": 30,
                },
            )
            result_url = output[0] if isinstance(output, list) else output
            logger.info("[pipeline] LoRA 추론 완료: %s", result_url)
            return result_url
        except ReplicateError as exc:
            if exc.status == 429 and attempt < 2:
                wait = 10 * (attempt + 1)
                logger.warning(
                    "[pipeline] 429 Rate Limit — %d초 후 재시도 (%d/3)", wait, attempt + 1
                )
                await asyncio.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# 공개 인터페이스
# ---------------------------------------------------------------------------

async def run_pipeline(image_url: str, breed_id: str, style_id: str) -> str:
    """
    강아지 미용 스타일 변환 파이프라인 실행 (LoRA 전용).

    LoRA 경로:
      1. base64 dataURL이면 Cloudinary public URL로 사전 변환
      2. LoRA img2img 추론 (학습된 LoRA가 없으면 ValueError)
      3. 추론 결과 URL → Cloudinary 저장 → URL 반환

    Args:
        image_url: 원본 강아지 이미지 URL (public URL 또는 data: 스킴)
        breed_id:  견종 ID (style_prompts.BREEDS 키)
        style_id:  스타일 ID (해당 견종의 styles 키)

    Returns:
        Cloudinary에 저장된 변환 결과 이미지의 공개 URL

    Raises:
        ValueError: breed_id / style_id 가 존재하지 않을 때, 또는 LoRA 미학습 시
        RuntimeError: Replicate API 또는 Cloudinary 호출 실패 시
    """
    # 1. 프롬프트 조회
    prompt_data = get_prompt(breed_id, style_id)
    if prompt_data is None:
        raise ValueError(f"존재하지 않는 breed_id 또는 style_id: {breed_id}/{style_id}")

    prompt = prompt_data["prompt"]
    negative_prompt = prompt_data["negative_prompt"]

    # 2. LoRA 확인 — 학습된 LoRA가 없으면 서비스 불가
    lora_entry = lora_training.get_lora_entry(breed_id, style_id)
    if (
        lora_entry is None
        or lora_entry.get("status") != "ready"
        or not lora_entry.get("version")
    ):
        raise ValueError(f"학습된 LoRA가 없습니다: {breed_id}/{style_id}. 먼저 LoRA 학습을 완료하세요.")

    try:
        # 3. base64 dataURL → Cloudinary 변환 (필요한 경우)
        if image_url.startswith("data:"):
            image_url = _upload_base64_to_cloudinary(image_url)

        # 4. LoRA img2img 추론
        trigger = lora_entry["trigger_word"]
        lora_prompt = f"{trigger} {prompt}"
        model_ref = f"{lora_entry['replicate_model']}:{lora_entry['version']}"
        logger.info("[pipeline] LoRA 추론 시작 (model=%s)", model_ref)
        transformed_url = await _run_with_lora(image_url, model_ref, lora_prompt, negative_prompt)

        # 5. Cloudinary 저장
        result_url = await _upload_url_to_cloudinary(transformed_url, breed_id, style_id)

    except ValueError:
        raise
    except Exception as exc:
        logger.error("[pipeline] 파이프라인 실패 (breed=%s, style=%s): %s", breed_id, style_id, exc, exc_info=True)
        raise RuntimeError(f"AI 파이프라인 처리 중 오류가 발생했습니다: {exc}") from exc

    return result_url
