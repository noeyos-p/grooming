"""이미지 공통 유틸리티.

gemini_pipeline, vertex_imagen_pipeline, inpaint_pipeline에서 공유하는
이미지 처리 헬퍼 함수를 한 곳에서 관리한다.
"""

import io
import logging

import pillow_heif
pillow_heif.register_heif_opener()

from PIL import Image

logger = logging.getLogger(__name__)


def _detect_mime_type(image_bytes: bytes) -> str:
    """이미지 bytes의 magic number로 MIME 타입을 감지한다.

    HEIC, JPEG, PNG, WEBP를 지원하며, 알 수 없는 경우 image/jpeg를 반환한다.
    """
    if image_bytes[:4] == b"\x89PNG":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
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
            logger.info("[image_utils] HEIC → JPEG 변환 완료")
            return buf.getvalue(), True
        except Exception as exc:
            logger.warning("[image_utils] HEIC → JPEG 변환 실패: %s — 원본 bytes 사용", exc)
    return image_bytes, False
