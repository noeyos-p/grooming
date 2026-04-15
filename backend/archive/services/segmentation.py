# 이 파일은 현재 사용되지 않습니다.
# Grounded SAM은 ControlNet+compositing 경로와 함께 폐기됐습니다.
# 유일한 추론 경로는 LoRA img2img입니다 (ai_pipeline.py 참고).

"""
SAM(Segment Anything Model) 기반 강아지 배경 분리 서비스.
실제 SAM 호출은 ai_pipeline.py 내 Replicate API를 통해 이루어진다.
이 모듈은 분리 로직의 진입점 역할을 한다.

사용 모델: schananas/grounded_sam (Grounded-DINO + SAM 통합)
  - mask_prompt: 마스크를 생성할 영역 텍스트 (흰색=변환 대상)
  - negative_mask_prompt: 마스크에서 제외할 영역 텍스트
  - adjustment_factor: 음수=침식(erosion), 양수=팽창(dilation)
    얼굴 경계 안전 마진을 위해 +10 팽창 적용 (얼굴 마스크 기준)

마스크 전략 변경 이력:
  - 이전: mask_prompt="dog fur" → SAM이 털 경계를 불명확하게 인식, 마스크 품질 불량
  - 현재: mask_prompt="dog face" → 얼굴 탐지가 털 탐지보다 정확
    ai_pipeline.py에서 ImageOps.invert로 반전 후 합성 (얼굴=보존, 털=변환)

출력: [마스크 URL, 마스크 적용 이미지 URL] 중 index 0이 마스크
"""

import logging

import replicate
from replicate.exceptions import ReplicateError

logger = logging.getLogger(__name__)

# 검증된 버전 해시 고정 (2026-04 기준)
# schananas/grounded_sam: Grounded-DINO + SAM 통합, mask_prompt 텍스트로 마스크 생성
# 입력: image, mask_prompt, negative_mask_prompt, adjustment_factor
# 출력: [마스크_URL, 시각화_URL] (iterator)
_MODEL_GROUNDED_SAM = (
    "schananas/grounded_sam:"
    "ee871c19efb1941f55f66a3d7d960428c8a5afcb77449547fe8e5a3ab9ebc21c"
)

# adjustment_factor: 양수 값으로 얼굴 마스크를 팽창시켜 경계 바깥으로 안전 마진 확보
# 반전 후 합성하므로 팽창 = 얼굴 보존 영역이 바깥으로 확장 = 얼굴 경계 누락 방지
_MASK_DILATION = 15


async def segment_dog(image_url: str) -> str | None:
    """
    Grounded SAM으로 강아지 털/몸통 마스크를 생성한다.

    Args:
        image_url: Replicate가 접근 가능한 public URL

    Returns:
        마스크 이미지 URL (흰색=변환 영역, 검은색=보존 영역).
        SAM 호출 실패 시 None 반환 — 호출자가 폴백 처리.

    마스크 전략:
        mask_prompt="dog face, dog head, eyes, nose, forehead, muzzle"
        negative_mask_prompt="dog body, dog fur, dog coat, background"
        adjustment_factor=+10 (팽창: 얼굴 경계 바깥으로 10px 안전 마진 확보)

        반환된 마스크(흰색=얼굴)는 ai_pipeline.py에서 ImageOps.invert로 반전 후 합성.
        반전 결과: 흰색=털·배경(변환), 검은색=얼굴(원본 보존)
    """
    logger.info("[segmentation] Grounded SAM 마스크 생성 시작: %s", image_url)
    try:
        output = await replicate.async_run(
            _MODEL_GROUNDED_SAM,
            input={
                "image": image_url,
                "mask_prompt": "dog face, dog head, eyes, nose, forehead, muzzle",
                "negative_mask_prompt": "dog body, dog fur, dog coat, background",
                "adjustment_factor": _MASK_DILATION,
            },
        )
        # 출력은 iterator — 리스트로 수집
        results = list(output) if not isinstance(output, list) else output
        if not results:
            logger.warning("[segmentation] Grounded SAM 출력이 비어 있음 — None 반환")
            return None
        # index 0: 마스크 URL (흰색=변환, 검은색=보존)
        mask_url = str(results[0])
        logger.info("[segmentation] 마스크 생성 완료: %s", mask_url)
        return mask_url
    except ReplicateError as exc:
        logger.warning(
            "[segmentation] Grounded SAM ReplicateError (status=%s): %s — None 반환",
            getattr(exc, "status", "?"),
            exc,
        )
        return None
    except Exception as exc:
        logger.warning("[segmentation] Grounded SAM 예외: %s — None 반환", exc)
        return None
