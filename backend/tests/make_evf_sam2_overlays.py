"""
EVF-SAM2 probe 의 mask 3장을 원본 위에 overlay 하여 시각 검증 이미지 생성.

육안 체크 3가지:
 1) mask 가 실제 눈·코 위치를 제대로 덮는가
 2) left/right 라벨 스왑이 단순 관례 차이인가
 3) 마스크 크기가 anchor 용도로 과도하지 않은가

절차:
 1) run_evf_sam2_probe.py 가 뽑은 각 파트 대표 mask URL (rep=1 기준) 과 원본 이미지를 사용
 2) 각 파트별로 원본 위에 mask 를 컬러(반투명) 로 overlay 후 Cloudinary 업로드
 3) 결합 overlay(3개 파트 동시 표시) 도 1장 추가

입력:
 - 원본 이미지: /Users/soyeon/Downloads/IMG_7641.jpg
 - mask URL 3개는 상수로 하드코딩 (run_evf_sam2_probe.py 직전 실행 결과 값)

stdout 에 JSON 으로 Cloudinary URL 4개 출력. stderr 에 로그.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from pathlib import Path

import cloudinary
import cloudinary.uploader
import httpx
import numpy as np
import pillow_heif
from dotenv import load_dotenv
from PIL import Image

pillow_heif.register_heif_opener()

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

load_dotenv(_BACKEND_ROOT / ".env")

# ── 입력 ──────────────────────────────────────────────────────────────────────

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")

# run_evf_sam2_probe.py 방금 실행 결과의 rep=1 mask URL
MASK_URLS: dict[str, str] = {
    "left_eye": "https://fal.media/files/tiger/fN-j14Klj93yYYmma1JCS_75e30c8bb88e4ea3bddb0b8fb3725c67.png",
    "right_eye": "https://fal.media/files/penguin/d_bhNBRlldNt4IE-VaD4U_e2fd04168bc44983b3bdf5c9f11aa805.png",
    "nose": "https://fal.media/files/tiger/VQDg7H74w3ODsOOx8bI39_985d86ec1e094c23b88c792c1b06006b.png",
}

# overlay 색상 (RGB, 반투명 알파 사용)
PART_COLORS: dict[str, tuple[int, int, int]] = {
    "left_eye": (255, 0, 0),      # 빨강
    "right_eye": (0, 255, 0),     # 초록
    "nose": (0, 0, 255),          # 파랑
}
OVERLAY_ALPHA = 0.45

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("make_evf_sam2_overlays")


def _configure_cloudinary() -> None:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )


def _download(url: str) -> bytes:
    with httpx.Client() as client:
        resp = client.get(url, timeout=60.0)
        resp.raise_for_status()
        return resp.content


def _load_mask(mask_bytes: bytes, target_size: tuple[int, int]) -> np.ndarray:
    """mask PNG → boolean(H, W) array, 필요 시 target_size 로 resize."""
    img = Image.open(io.BytesIO(mask_bytes)).convert("L")
    if img.size != target_size:
        img = img.resize(target_size, Image.NEAREST)
    arr = np.asarray(img, dtype=np.uint8)
    return arr >= 128


def _overlay_single(
    base_rgb: np.ndarray,
    mask: np.ndarray,
    color: tuple[int, int, int],
    alpha: float,
) -> np.ndarray:
    out = base_rgb.copy()
    color_arr = np.array(color, dtype=np.float32)
    out_f = out.astype(np.float32)
    blend = out_f[mask] * (1 - alpha) + color_arr * alpha
    out_f[mask] = blend
    return np.clip(out_f, 0, 255).astype(np.uint8)


def _draw_outline(
    rgb: np.ndarray,
    mask: np.ndarray,
    color: tuple[int, int, int],
    thickness: int = 4,
) -> np.ndarray:
    """mask 경계(윤곽선) 를 색상으로 그어 mask 범위를 시각화."""
    from scipy.ndimage import binary_dilation, binary_erosion  # noqa: PLC0415
    # scipy 없으면 대체 — 하지만 이 스파이크는 scipy 의존 없음. numpy 만으로 rough edge.
    # (실제로는 PIL + ImageFilter.FIND_EDGES 경로가 더 안전)
    _ = binary_dilation, binary_erosion
    return rgb  # fallback — 쓰지 않음


def _make_outline_from_mask(mask: np.ndarray, thickness: int = 4) -> np.ndarray:
    """numpy 만으로 mask 경계 추출 — shift XOR 방식."""
    up = np.roll(mask, -thickness, axis=0)
    down = np.roll(mask, thickness, axis=0)
    left = np.roll(mask, -thickness, axis=1)
    right = np.roll(mask, thickness, axis=1)
    dilated = mask | up | down | left | right
    return dilated & ~mask  # 외곽 링


def _apply_part_overlay(
    base_rgb: np.ndarray,
    mask: np.ndarray,
    color: tuple[int, int, int],
) -> np.ndarray:
    """fill(반투명) + outline(진한 링) 둘 다 적용."""
    filled = _overlay_single(base_rgb, mask, color, OVERLAY_ALPHA)
    outline = _make_outline_from_mask(mask, thickness=6)
    filled[outline] = np.array(color, dtype=np.uint8)
    return filled


def _save_jpeg_bytes(rgb: np.ndarray, quality: int = 90) -> bytes:
    img = Image.fromarray(rgb, mode="RGB")
    # 최대 변 2000px 로 축소하여 Cloudinary 10MB 한계 회피 — 육안 검증용이므로 충분
    max_side = 2000
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _upload(img_bytes: bytes, tag: str) -> str:
    result = cloudinary.uploader.upload(
        img_bytes,
        folder="grooming-style/probe/evf-sam2-overlay",
        resource_type="image",
        format="jpg",
        public_id=None,
        tags=[tag],
    )
    return result["secure_url"]


def _mask_stats(mask: np.ndarray, img_w: int, img_h: int) -> dict:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return {"area_px": 0}
    return {
        "area_px": int(xs.size),
        "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
        "cx": float(xs.mean()),
        "cy": float(ys.mean()),
        "area_ratio": round(xs.size / (img_w * img_h), 5),
    }


def main() -> None:
    for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"):
        if not os.getenv(k):
            print(json.dumps({"error": f"{k} 환경변수 미설정"}), flush=True)
            sys.exit(2)
    _configure_cloudinary()

    orig_bytes = IMAGE_PATH.read_bytes()
    orig = Image.open(io.BytesIO(orig_bytes)).convert("RGB")
    img_w, img_h = orig.size
    base_rgb = np.asarray(orig, dtype=np.uint8)
    logger.info("원본: %s %dx%d", IMAGE_PATH.name, img_w, img_h)

    # 1) 파트별 단독 overlay 3장
    per_part_urls: dict[str, dict] = {}
    combined_rgb = base_rgb.copy()
    for name, url in MASK_URLS.items():
        logger.info("[%s] mask 다운로드: %s", name, url)
        mask_bytes = _download(url)
        mask = _load_mask(mask_bytes, (img_w, img_h))
        stats = _mask_stats(mask, img_w, img_h)
        logger.info("[%s] mask stats: %s", name, stats)

        overlay = _apply_part_overlay(base_rgb, mask, PART_COLORS[name])
        jpg = _save_jpeg_bytes(overlay)
        up_url = _upload(jpg, tag=f"evf-sam2-{name}")
        per_part_urls[name] = {"overlay_url": up_url, **stats}
        logger.info("[%s] 업로드 완료: %s", name, up_url)

        combined_rgb = _apply_part_overlay(combined_rgb, mask, PART_COLORS[name])

    # 2) 결합 overlay 1장
    combined_jpg = _save_jpeg_bytes(combined_rgb)
    combined_url = _upload(combined_jpg, tag="evf-sam2-combined")
    logger.info("결합 overlay 업로드 완료: %s", combined_url)

    result = {
        "image_size": [img_w, img_h],
        "per_part": per_part_urls,
        "combined_overlay_url": combined_url,
        "legend": {
            "left_eye": "red",
            "right_eye": "green",
            "nose": "blue",
        },
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
