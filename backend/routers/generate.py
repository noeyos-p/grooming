import logging
import time

from fastapi import APIRouter, HTTPException

from models.breed import GenerateRequest, GenerateResponse
from services.gemini_pipeline import run_gemini_pipeline
from services.style_prompts import get_prompt
from services.vertex_imagen_pipeline import run_vertex_imagen_pipeline
from services.vertex_imagen_training import get_imagen_entry

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest) -> GenerateResponse:
    """
    강아지 사진을 받아 지정된 견종+스타일로 AI 변환한 결과 URL을 반환한다.

    Imagen-first 전략:
      - 해당 breed+style의 Vertex AI Imagen 튜닝 모델이 준비("ready")된 경우 Imagen 파이프라인 사용
      - 준비되지 않은 경우 Gemini 파이프라인으로 폴백

    API 응답 소요 시간: 15~60초 (모델 콜드 스타트 포함)
    """
    logger.info(
        "[generate] 요청 수신 — breed_id=%s, style_id=%s",
        request.breed_id,
        request.style_id,
    )

    start_time = time.monotonic()

    # breed_id / style_id 사전 검증 (422 적합 — 파이프라인 진입 전)
    prompt_check = get_prompt(request.breed_id, request.style_id)
    if prompt_check is None:
        raise HTTPException(
            status_code=422,
            detail=f"존재하지 않는 breed_id 또는 style_id: {request.breed_id}/{request.style_id}",
        )

    # Imagen-first 라우팅 판단
    imagen_entry = get_imagen_entry(request.breed_id, request.style_id)
    use_imagen = (
        imagen_entry is not None
        and imagen_entry.get("status") == "ready"
        and bool(imagen_entry.get("endpoint_id"))
    )

    try:
        if use_imagen:
            logger.info("[generate] Imagen 3 파이프라인 선택")
            result_url = await run_vertex_imagen_pipeline(
                image_url=request.image_url,
                breed_id=request.breed_id,
                style_id=request.style_id,
            )
        else:
            logger.info("[generate] Gemini 파이프라인 선택 (fallback)")
            result_url = await run_gemini_pipeline(
                image_url=request.image_url,
                breed_id=request.breed_id,
                style_id=request.style_id,
            )
    except (ValueError, RuntimeError) as exc:
        # SDK 파라미터 오류 및 파이프라인 내부 오류 — 원시 오류를 클라이언트에 노출하지 않는다
        logger.error("[generate] 파이프라인 오류: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="이미지 변환 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        ) from exc

    processing_time = time.monotonic() - start_time
    logger.info("[generate] 완료 — %.2fs, url=%s", processing_time, result_url)

    return GenerateResponse(result_url=result_url, processing_time=processing_time)
