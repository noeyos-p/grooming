"""
Vertex AI Imagen 3 Style Tuning 잡 관리.

모든 Vertex AI 튜닝 API 호출은 반드시 이 모듈을 통해서만 이루어진다.
학습 상태 및 완료된 endpoint 정보는 config/imagen_registry.json에 영속 저장한다.

레지스트리: backend/config/imagen_registry.json
키: "{breed_id}_{style_id}" (예: "maltese_teddy_cut")
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import vertexai
from vertexai.preview.vision_models import ImageGenerationModel

from services.style_prompts import BREEDS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 레지스트리 파일 경로 (backend/config/imagen_registry.json)
# ---------------------------------------------------------------------------
_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "imagen_registry.json"

# ---------------------------------------------------------------------------
# Vertex AI Imagen 3 베이스 모델 (스타일 튜닝용)
# ---------------------------------------------------------------------------
_IMAGEN_BASE_MODEL = "imagegeneration@006"


# ---------------------------------------------------------------------------
# 레지스트리 I/O
# ---------------------------------------------------------------------------

def load_registry() -> dict:
    """imagen_registry.json을 읽어 dict로 반환한다. 파일 없거나 비어 있으면 {} 반환."""
    try:
        text = _REGISTRY_PATH.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        return json.loads(text)
    except FileNotFoundError:
        logger.warning("[vertex_imagen_training] 레지스트리 파일 없음, 빈 dict 반환")
        return {}
    except json.JSONDecodeError as exc:
        logger.error("[vertex_imagen_training] 레지스트리 JSON 파싱 실패: %s", exc)
        return {}


def save_registry(data: dict) -> None:
    """dict를 imagen_registry.json에 저장한다 (indent=2)."""
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("[vertex_imagen_training] 레지스트리 저장 완료")


def get_imagen_entry(breed_id: str, style_id: str) -> dict | None:
    """특정 breed+style의 레지스트리 항목 반환. 없으면 None."""
    registry = load_registry()
    return registry.get(f"{breed_id}_{style_id}")


# ---------------------------------------------------------------------------
# 튜닝 시작
# ---------------------------------------------------------------------------

async def start_tuning(breed_id: str, style_id: str) -> str:
    """Vertex AI Imagen 3 스타일 튜닝 잡을 시작한다.

    style_prompts.BREEDS에서 reference_images_gcs 필드를 읽어 GCS 경로를 획득한다.
    레지스트리에 status="tuning"을 기록하고 tuning_job_name을 반환한다.

    Args:
        breed_id: 견종 ID (style_prompts.BREEDS 키)
        style_id: 스타일 ID (해당 견종의 styles 키)

    Returns:
        Vertex AI tuning job name (str)

    Raises:
        ValueError: GOOGLE_CLOUD_PROJECT 미설정 / reference_images_gcs 없음 /
                    유효하지 않은 breed_id 또는 style_id
        RuntimeError: Vertex AI API 호출 실패
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project:
        raise ValueError("GOOGLE_CLOUD_PROJECT 환경변수가 설정되지 않았습니다.")

    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    # breed/style 유효성 검증 및 GCS 경로 획득
    breed_data = BREEDS.get(breed_id)
    if breed_data is None:
        raise ValueError(f"존재하지 않는 breed_id: {breed_id}")

    style_data = breed_data.get("styles", {}).get(style_id)
    if style_data is None:
        raise ValueError(f"breed '{breed_id}'에 존재하지 않는 style_id: {style_id}")

    gcs_uri = style_data.get("reference_images_gcs")
    if not gcs_uri:
        raise ValueError(
            f"'{breed_id}/{style_id}'의 reference_images_gcs가 설정되지 않았습니다. "
            "style_prompts.py에서 GCS 경로를 먼저 입력해주세요."
        )

    logger.info(
        "[vertex_imagen_training] 튜닝 시작 — breed=%s, style=%s, gcs=%s",
        breed_id,
        style_id,
        gcs_uri,
    )

    # Vertex AI 초기화
    def _init_and_tune() -> str:
        vertexai.init(project=project, location=location)
        model = ImageGenerationModel.from_pretrained(_IMAGEN_BASE_MODEL)
        tuning_job = model.tune_model(
            training_dataset=gcs_uri,
            model_display_name=f"grooming-{breed_id}-{style_id}".replace("_", "-"),
        )
        return tuning_job.tuning_job_name

    try:
        tuning_job_name: str = await asyncio.to_thread(_init_and_tune)
    except Exception as exc:
        logger.error(
            "[vertex_imagen_training] 튜닝 잡 생성 실패 (breed=%s, style=%s): %s",
            breed_id,
            style_id,
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"Vertex AI 튜닝 잡 생성 중 오류: {exc}") from exc

    # 레지스트리 저장
    registry = load_registry()
    registry[f"{breed_id}_{style_id}"] = {
        "tuning_job_name": tuning_job_name,
        "endpoint_id": None,
        "gcs_reference_images": gcs_uri,
        "status": "tuning",
        "tuned_at": None,
    }
    save_registry(registry)

    logger.info(
        "[vertex_imagen_training] 레지스트리 업데이트 완료 (tuning_job_name=%s)",
        tuning_job_name,
    )
    return tuning_job_name


# ---------------------------------------------------------------------------
# 튜닝 상태 조회
# ---------------------------------------------------------------------------

async def get_tuning_status(tuning_job_name: str) -> dict:
    """Vertex AI 튜닝 잡 상태를 조회하고 레지스트리를 업데이트한다.

    완료 시 endpoint_id와 status="ready", tuned_at을 레지스트리에 기록한다.

    Args:
        tuning_job_name: Vertex AI 튜닝 잡 이름
                         (예: "projects/123/locations/us-central1/tuningJobs/456")

    Returns:
        {"status": str, "endpoint_id": str | None}

    Raises:
        RuntimeError: Vertex AI API 호출 실패
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    def _poll_status() -> tuple[str, str | None]:
        """(job_state, endpoint_id) 반환."""
        vertexai.init(project=project, location=location)
        # google-cloud-aiplatform의 PipelineJob/TuningJob 상태 폴링
        from google.cloud import aiplatform
        aiplatform.init(project=project, location=location)

        # TuningJob 리소스 직접 조회
        client = aiplatform.gapic.ModelServiceClient(
            client_options={"api_endpoint": f"{location}-aiplatform.googleapis.com"}
        )
        # tuning_job_name을 기반으로 상태 확인 — Vertex AI SDK의 get_tuning_job 사용
        from google.cloud.aiplatform_v1 import TuningJobServiceClient
        tuning_client = TuningJobServiceClient(
            client_options={"api_endpoint": f"{location}-aiplatform.googleapis.com"}
        )
        job = tuning_client.get_tuning_job(name=tuning_job_name)
        state_name = job.state.name  # e.g. "JOB_STATE_SUCCEEDED"

        endpoint_id: str | None = None
        if hasattr(job, "tuned_model") and job.tuned_model:
            endpoint_id = getattr(job.tuned_model, "endpoint", None)

        return state_name, endpoint_id

    try:
        state_name, endpoint_id = await asyncio.to_thread(_poll_status)
    except Exception as exc:
        logger.error(
            "[vertex_imagen_training] 튜닝 상태 조회 실패 (job=%s): %s",
            tuning_job_name,
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"Vertex AI 튜닝 상태 조회 중 오류: {exc}") from exc

    logger.info(
        "[vertex_imagen_training] 튜닝 상태 — job=%s, state=%s, endpoint=%s",
        tuning_job_name,
        state_name,
        endpoint_id,
    )

    # 레지스트리에서 해당 tuning_job_name에 대응하는 항목 탐색
    registry = load_registry()
    entry_key: str | None = None
    entry: dict | None = None
    for key, val in registry.items():
        if val.get("tuning_job_name") == tuning_job_name:
            entry_key = key
            entry = val
            break

    if entry is None:
        logger.warning(
            "[vertex_imagen_training] 레지스트리에서 tuning_job_name=%s 항목 없음",
            tuning_job_name,
        )
        return {"status": state_name, "endpoint_id": endpoint_id}

    # 상태에 따라 레지스트리 업데이트
    if state_name == "JOB_STATE_SUCCEEDED":
        entry["status"] = "ready"
        entry["endpoint_id"] = endpoint_id
        entry["tuned_at"] = datetime.now().isoformat()
        logger.info(
            "[vertex_imagen_training] 튜닝 완료 — endpoint_id=%s", endpoint_id
        )
    elif state_name in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED"):
        entry["status"] = "failed"
        logger.warning(
            "[vertex_imagen_training] 튜닝 실패/취소 (job=%s, state=%s)",
            tuning_job_name,
            state_name,
        )

    if entry_key is not None:
        registry[entry_key] = entry
        save_registry(registry)

    return {
        "status": entry["status"],
        "endpoint_id": entry.get("endpoint_id"),
    }
