"""관리자 API — LoRA 학습 관리 및 Vertex AI Imagen 스타일 튜닝 관리 엔드포인트.

인증 없는 관리용 API (서비스 정책상 auth 미적용).
LoRA 학습 시작, 상태 조회, 레지스트리 조회/삭제,
Vertex AI Imagen 튜닝 시작, 상태 조회, 레지스트리 조회/삭제를 제공한다.
"""

import logging

import cloudinary
import cloudinary.uploader
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.params import File, Form

from services import lora_training, style_prompts
from services import vertex_imagen_training

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# POST /api/admin/train — LoRA 학습 시작
# ---------------------------------------------------------------------------

@router.post("/train")
async def start_lora_training(
    breed_id: str = Form(...),
    style_id: str = Form(...),
    images: UploadFile = File(...),
) -> dict:
    """견종+스타일별 LoRA 학습을 시작한다.

    - images: 학습용 이미지 묶음 zip 파일 (multipart/form-data)
    - breed_id, style_id: style_prompts.BREEDS에 존재하는 값이어야 함
    """
    # 입력값 검증 — style_prompts.BREEDS 기준
    breed_data = style_prompts.BREEDS.get(breed_id)
    if breed_data is None:
        raise HTTPException(status_code=422, detail=f"존재하지 않는 breed_id: {breed_id}")
    if style_id not in breed_data.get("styles", {}):
        raise HTTPException(
            status_code=422,
            detail=f"breed '{breed_id}'에 존재하지 않는 style_id: {style_id}",
        )

    # zip 파일 읽기
    zip_bytes = await images.read()
    logger.info(
        "[admin] 학습 zip 수신 (breed=%s, style=%s, size=%d bytes)",
        breed_id, style_id, len(zip_bytes),
    )

    # Cloudinary에 raw 리소스로 업로드
    try:
        upload_result = cloudinary.uploader.upload(
            zip_bytes,
            folder="grooming-style/training-zips",
            resource_type="raw",
            format="zip",
            overwrite=True,
        )
        cloudinary_url: str = upload_result["secure_url"]
        logger.info("[admin] zip Cloudinary 업로드 완료: %s", cloudinary_url)
    except Exception as exc:
        logger.error("[admin] zip Cloudinary 업로드 실패: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"학습 이미지 업로드 중 오류가 발생했습니다: {exc}",
        ) from exc

    # 학습 시작
    try:
        training_id = await lora_training.start_training(breed_id, style_id, cloudinary_url)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"training_id": training_id, "message": "학습 시작됨"}


# ---------------------------------------------------------------------------
# GET /api/admin/train/{training_id}/status — 학습 상태 조회
# ---------------------------------------------------------------------------

@router.get("/train/{training_id}/status")
async def get_training_status(training_id: str) -> dict:
    """특정 training_id의 Replicate 학습 상태를 조회한다."""
    try:
        return await lora_training.get_training_status(training_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/admin/lora — 전체 레지스트리 조회
# ---------------------------------------------------------------------------

@router.get("/lora")
async def list_lora_registry() -> dict:
    """전체 LoRA 레지스트리를 반환한다."""
    return lora_training.load_registry()


# ---------------------------------------------------------------------------
# DELETE /api/admin/lora/{breed_id}/{style_id} — 레지스트리 항목 삭제
# ---------------------------------------------------------------------------

@router.delete("/lora/{breed_id}/{style_id}")
async def delete_lora_entry(breed_id: str, style_id: str) -> dict:
    """레지스트리에서 특정 breed+style의 LoRA 항목을 삭제한다."""
    key = f"{breed_id}_{style_id}"
    registry = lora_training.load_registry()

    if key not in registry:
        raise HTTPException(
            status_code=404,
            detail=f"레지스트리에 항목이 없습니다: {key}",
        )

    del registry[key]
    lora_training.save_registry(registry)
    logger.info("[admin] 레지스트리 항목 삭제 완료: %s", key)
    return {"message": "삭제됨"}


# ---------------------------------------------------------------------------
# POST /api/admin/imagen/tune — Vertex AI Imagen 스타일 튜닝 시작
# ---------------------------------------------------------------------------

@router.post("/imagen/tune")
async def start_imagen_tuning(
    breed_id: str = Form(...),
    style_id: str = Form(...),
) -> dict:
    """Vertex AI Imagen 3 스타일 튜닝 잡을 시작한다.

    - breed_id, style_id: style_prompts.BREEDS에 존재하는 값이어야 함
    - style_prompts.BREEDS의 reference_images_gcs 필드에 GCS URI가 설정되어 있어야 함
    """
    # 입력값 검증 — style_prompts.BREEDS 기준
    breed_data = style_prompts.BREEDS.get(breed_id)
    if breed_data is None:
        raise HTTPException(status_code=422, detail=f"존재하지 않는 breed_id: {breed_id}")
    if style_id not in breed_data.get("styles", {}):
        raise HTTPException(
            status_code=422,
            detail=f"breed '{breed_id}'에 존재하지 않는 style_id: {style_id}",
        )

    try:
        tuning_job_name = await vertex_imagen_training.start_tuning(breed_id, style_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info("[admin] Imagen 튜닝 시작 완료: %s", tuning_job_name)
    return {"tuning_job_name": tuning_job_name, "message": "Imagen 스타일 튜닝 시작됨"}


# ---------------------------------------------------------------------------
# GET /api/admin/imagen/tune/{tuning_job_name}/status — 튜닝 상태 조회
# ---------------------------------------------------------------------------

@router.get("/imagen/tune/{tuning_job_name:path}/status")
async def get_imagen_tuning_status(tuning_job_name: str) -> dict:
    """특정 Vertex AI Imagen 튜닝 잡의 상태를 조회한다.

    {tuning_job_name:path} — Vertex AI 리소스명에 '/'가 포함되므로 path 타입 사용.
    예: projects/123/locations/us-central1/tuningJobs/456
    """
    try:
        return await vertex_imagen_training.get_tuning_status(tuning_job_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /api/admin/imagen — 전체 Imagen 레지스트리 조회
# ---------------------------------------------------------------------------

@router.get("/imagen")
async def list_imagen_registry() -> dict:
    """전체 Imagen 레지스트리를 반환한다."""
    return vertex_imagen_training.load_registry()


# ---------------------------------------------------------------------------
# DELETE /api/admin/imagen/{breed_id}/{style_id} — Imagen 레지스트리 항목 삭제
# ---------------------------------------------------------------------------

@router.delete("/imagen/{breed_id}/{style_id}")
async def delete_imagen_entry(breed_id: str, style_id: str) -> dict:
    """레지스트리에서 특정 breed+style의 Imagen 항목을 삭제한다."""
    key = f"{breed_id}_{style_id}"
    registry = vertex_imagen_training.load_registry()

    if key not in registry:
        raise HTTPException(
            status_code=404,
            detail=f"Imagen 레지스트리에 항목이 없습니다: {key}",
        )

    del registry[key]
    vertex_imagen_training.save_registry(registry)
    logger.info("[admin] Imagen 레지스트리 항목 삭제 완료: %s", key)
    return {"message": "삭제됨"}
