"""LoRA 학습 관리 서비스 — Replicate Training API 래퍼.

모든 Replicate Training API 호출은 반드시 이 모듈을 통해서만 이루어진다.
학습 상태 및 완료된 LoRA 버전 정보는 config/lora_registry.json에 영속 저장한다.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import replicate

from services import style_prompts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 레지스트리 파일 경로 (backend/config/lora_registry.json)
# ---------------------------------------------------------------------------
_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "lora_registry.json"

# ---------------------------------------------------------------------------
# Replicate Training 기반 모델 (SDXL LoRA 학습용)
# ---------------------------------------------------------------------------
_SDXL_TRAINABLE_VERSION = (
    "stability-ai/sdxl:7762fd07cf82c948538e41f63f77d685e02b063e37e496e96eefd46c929f9bdc"
)


# ---------------------------------------------------------------------------
# 레지스트리 I/O
# ---------------------------------------------------------------------------

def load_registry() -> dict:
    """lora_registry.json을 읽어 dict로 반환한다. 파일 없거나 비어 있으면 {} 반환."""
    try:
        text = _REGISTRY_PATH.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        return json.loads(text)
    except FileNotFoundError:
        logger.warning("[lora_training] 레지스트리 파일 없음, 빈 dict 반환")
        return {}
    except json.JSONDecodeError as exc:
        logger.error("[lora_training] 레지스트리 JSON 파싱 실패: %s", exc)
        return {}


def save_registry(data: dict) -> None:
    """dict를 lora_registry.json에 저장한다 (indent=2)."""
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("[lora_training] 레지스트리 저장 완료")


def get_lora_entry(breed_id: str, style_id: str) -> dict | None:
    """특정 breed+style의 레지스트리 항목 반환. 없으면 None."""
    registry = load_registry()
    return registry.get(f"{breed_id}_{style_id}")


# ---------------------------------------------------------------------------
# 학습 시작
# ---------------------------------------------------------------------------

async def start_training(breed_id: str, style_id: str, images_zip_url: str) -> str:
    """Replicate Training API로 SDXL LoRA 학습을 시작한다.

    Args:
        breed_id:       견종 ID (style_prompts.BREEDS 키)
        style_id:       스타일 ID (해당 견종의 styles 키)
        images_zip_url: 학습용 이미지 zip 파일의 Cloudinary 공개 URL

    Returns:
        Replicate training ID (str)

    Raises:
        ValueError: REPLICATE_USERNAME 환경변수 미설정 시
        RuntimeError: Replicate API 호출 실패 시
    """
    replicate_username = os.environ.get("REPLICATE_USERNAME", "")
    if not replicate_username:
        raise ValueError("REPLICATE_USERNAME 환경변수가 설정되지 않았습니다.")

    # 모델명: grooming-{breed_id}-{style_id} (언더스코어 → 하이픈)
    model_name = f"grooming-{breed_id}-{style_id}".replace("_", "-")
    destination = f"{replicate_username}/{model_name}"

    logger.info("[lora_training] 학습 시작 — destination=%s", destination)

    # Replicate에 private 모델 생성 (이미 존재하면 무시)
    try:
        await asyncio.to_thread(
            replicate.models.create,
            owner=replicate_username,
            name=model_name,
            visibility="private",
            hardware="gpu-a40-large",
        )
        logger.info("[lora_training] Replicate 모델 생성: %s", destination)
    except Exception as exc:
        # 이미 존재하는 경우 등 — 무시하고 계속 진행
        logger.info("[lora_training] 모델 생성 스킵 (이미 존재할 수 있음): %s", exc)

    # 트리거 워드 — style_prompts에 정의된 값 우선, 없으면 패턴 생성
    breed_data = style_prompts.BREEDS.get(breed_id, {})
    style_data = breed_data.get("styles", {}).get(style_id, {})
    trigger_word: str = style_data.get(
        "trigger_word",
        f"GRMD{breed_id[:4].upper()}{style_id[:4].upper()}",
    )

    logger.info("[lora_training] 트리거 워드: %s", trigger_word)

    # Training 생성
    try:
        training = await asyncio.to_thread(
            replicate.trainings.create,
            version=_SDXL_TRAINABLE_VERSION,
            input={
                "input_images": images_zip_url,
                "token_string": trigger_word,
                "max_train_steps": 1000,
                "resolution": 1024,
                "use_face_detection_instead": False,
            },
            destination=destination,
        )
    except Exception as exc:
        logger.error("[lora_training] Training 생성 실패: %s", exc, exc_info=True)
        raise RuntimeError(f"Replicate Training 생성 중 오류: {exc}") from exc

    # 레지스트리 저장
    registry = load_registry()
    registry[f"{breed_id}_{style_id}"] = {
        "replicate_model": destination,
        "training_id": training.id,
        "trigger_word": trigger_word,
        "status": "training",
        "version": None,
        "trained_at": None,
    }
    save_registry(registry)

    logger.info("[lora_training] 레지스트리 업데이트 완료 (training_id=%s)", training.id)
    return training.id


# ---------------------------------------------------------------------------
# 학습 상태 조회
# ---------------------------------------------------------------------------

async def get_training_status(training_id: str) -> dict:
    """Replicate Training 상태를 조회하고 레지스트리를 업데이트한다.

    Args:
        training_id: Replicate training ID

    Returns:
        {"status": str, "version": str | None, "logs": str | None}

    Raises:
        RuntimeError: Replicate API 호출 실패 시
    """
    try:
        training = await asyncio.to_thread(replicate.trainings.get, training_id)
    except Exception as exc:
        logger.error("[lora_training] Training 조회 실패 (id=%s): %s", training_id, exc, exc_info=True)
        raise RuntimeError(f"Training 상태 조회 중 오류: {exc}") from exc

    # 레지스트리에서 해당 training_id에 대응하는 항목 탐색
    registry = load_registry()
    entry_key: str | None = None
    entry: dict | None = None
    for key, val in registry.items():
        if val.get("training_id") == training_id:
            entry_key = key
            entry = val
            break

    if entry is None:
        logger.warning("[lora_training] 레지스트리에서 training_id=%s 항목 없음", training_id)
        return {
            "status": training.status,
            "version": None,
            "logs": training.logs,
        }

    # 상태에 따라 레지스트리 업데이트
    if training.status == "succeeded":
        destination = entry["replicate_model"]
        try:
            model = await asyncio.to_thread(replicate.models.get, destination)
            version_id: str = model.latest_version.id
        except Exception as exc:
            logger.error(
                "[lora_training] 모델 버전 조회 실패 (destination=%s): %s",
                destination, exc, exc_info=True,
            )
            version_id = None

        entry["version"] = version_id
        entry["status"] = "ready"
        entry["trained_at"] = datetime.now().isoformat()
        logger.info("[lora_training] 학습 완료 — version=%s", version_id)

    elif training.status == "failed":
        entry["status"] = "failed"
        logger.warning("[lora_training] 학습 실패 (training_id=%s)", training_id)

    if entry_key is not None:
        registry[entry_key] = entry
        save_registry(registry)

    return {
        "status": training.status,
        "version": entry.get("version"),
        "logs": training.logs,
    }
