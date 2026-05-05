"""Silhouette IoU — Phase 34 v1.1 Step 1 (간이).

rembg(u2net) 기반 전경 마스크 추출로 원본/결과의 구조 안정성을 측정한다.
v1.0의 head_bbox IoU는 Gemini가 결과 이미지에 대해 loose bbox(80~100% 커버)를
반환해 민감도가 부족했음(Phase 34.1 확인). 실루엣 IoU가 bbox 대비 더 의미 있는
신호를 주는지가 v1.1 Step 1의 검증 포인트.

설계:
  - 모델: u2net (범용 전경 분리, ONNX). 첫 실행 시 ~170MB 다운로드
  - 결정론 — 동일 입력은 동일 출력
  - 외부 LLM 의존 없음
  - session은 프로세스 내 1회 생성 후 재사용

계산:
  1) 전경 마스크 추출(원본/결과)
  2) 해상도 정렬(NEAREST)
  3) 교집합/합집합으로 IoU
  4) (선택) head_bbox crop 기반 "머리 전용 IoU" — 전체 실루엣과 병렬 비교
"""

from __future__ import annotations

import io
import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_REMBG_MODEL = "u2net"
_session = None  # lazy init

_MASK_THRESHOLD = 128  # 0~255 → bool


def _get_session():
    """rembg 세션 lazy init. rembg 미설치면 ImportError 전파."""
    global _session
    if _session is None:
        from rembg import new_session  # 지연 import — 테스트가 rembg 없이도 동작
        _session = new_session(_REMBG_MODEL)
        logger.info("[silhouette_iou] rembg 세션 생성: model=%s", _REMBG_MODEL)
    return _session


def extract_foreground_mask(image_bytes: bytes) -> np.ndarray:
    """rembg로 전경 마스크 추출. True=전경.

    Returns:
        2D bool ndarray (H, W)
    """
    from rembg import remove  # 지연 import
    mask_bytes = remove(image_bytes, session=_get_session(), only_mask=True)
    mask_img = Image.open(io.BytesIO(mask_bytes)).convert("L")
    return np.asarray(mask_img) >= _MASK_THRESHOLD


def align_shape(
    orig_mask: np.ndarray, res_mask: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """두 마스크를 동일 해상도로 정렬 — 원본 shape에 맞춰 res를 NEAREST 리사이즈.

    NEAREST로만 리사이즈한다 — bool 마스크의 경계 계단을 bilinear로 흐리게
    만들지 않기 위함. 더 큰 이미지를 축소하는 경우에도 동일 규칙.
    """
    if orig_mask.shape == res_mask.shape:
        return orig_mask, res_mask
    target_h, target_w = orig_mask.shape
    res_img = Image.fromarray((res_mask.astype(np.uint8) * 255)).resize(
        (target_w, target_h), Image.NEAREST
    )
    return orig_mask, np.asarray(res_img) >= _MASK_THRESHOLD


def silhouette_iou(orig_mask: np.ndarray, res_mask: np.ndarray) -> float:
    """두 bool 마스크의 IoU. 정렬은 내부에서 처리."""
    a, b = align_shape(orig_mask, res_mask)
    inter = int(np.logical_and(a, b).sum())
    union = int(np.logical_or(a, b).sum())
    return float(inter / union) if union > 0 else 0.0


def crop_by_bbox(mask: np.ndarray, bbox: dict) -> np.ndarray:
    """bbox(픽셀 단위)로 마스크를 자른다. bbox는 {"xmin","ymin","xmax","ymax"}."""
    h, w = mask.shape
    x1 = max(0, min(w, bbox["xmin"]))
    y1 = max(0, min(h, bbox["ymin"]))
    x2 = max(x1, min(w, bbox["xmax"]))
    y2 = max(y1, min(h, bbox["ymax"]))
    return mask[y1:y2, x1:x2]


def mask_to_png_bytes(mask: np.ndarray) -> bytes:
    """bool 마스크를 흑/백 PNG로 직렬화 (디버그/저장용)."""
    img = Image.fromarray((mask.astype(np.uint8) * 255), mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
