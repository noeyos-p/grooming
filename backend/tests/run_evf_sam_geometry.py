"""
EVF-SAM2 geometry 모듈 독립 검증 — IMG_7641.jpg 고정 입력, 육안 판정용 overlay 3 장 업로드.

목적:
 - `services/evf_sam_geometry.get_anchor_masks()` 의 출력만으로 anchor-inpaint 에
   필요한 geometry 가 갖춰지는지 확인한다.
 - 코 cleanup 효능을 육안으로 판정할 수 있도록 "pre / post cleanup" 두 장을 나란히
   올리고, label alignment 결과는 최종 combined overlay 한 장으로 확인한다.

생성 산출물(Cloudinary 업로드):
 1) nose_pre_cleanup    — evf-sam 원본 nose 마스크를 원본 위에 그대로 overlay
 2) nose_post_cleanup   — largest_cc + opening + fill_holes + (opt) hull 이후 overlay
 3) combined_aligned    — left_eye(red) / right_eye(green) / nose(blue) 최종 라벨 기준 overlay

stdout 에 JSON 1 줄, stderr 에 raw 로그.

전제:
 - 환경변수 FAL_KEY, CLOUDINARY_* 필요
 - 테스트 이미지 고정: /Users/soyeon/Downloads/IMG_7641.jpg
 - 총 fal 호출 3 회 (parts × repeats 아님 — 각 파트 1 회씩)
"""

from __future__ import annotations

import asyncio
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

from services.evf_sam_geometry import (  # noqa: E402
    QualityFailure,
    get_anchor_masks,
)

# ── 설정 ──────────────────────────────────────────────────────────────────────

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")
USE_CONVEX_HULL = False  # 옵션 — 코가 과도하게 커지지 않도록 기본 OFF

PART_COLORS: dict[str, tuple[int, int, int]] = {
    "left_eye": (255, 0, 0),    # 빨강 — dog-perspective left (이미지 우측)
    "right_eye": (0, 255, 0),   # 초록 — dog-perspective right (이미지 좌측)
    "nose": (0, 0, 255),        # 파랑
}
OVERLAY_ALPHA = 0.45
OUTLINE_THICKNESS = 6

# Cloudinary 10MB 한계 회피용 JPEG 저장 옵션
_JPEG_QUALITY = 90
_JPEG_MAX_SIDE = 2000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("run_evf_sam_geometry")


# ── Cloudinary ────────────────────────────────────────────────────────────────


def _configure_cloudinary() -> None:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )


def _upload_bytes(image_bytes: bytes, folder: str, tag: str = "") -> str:
    result = cloudinary.uploader.upload(
        image_bytes,
        folder=folder,
        resource_type="image",
        format="jpg",
        tags=[tag] if tag else None,
    )
    return result["secure_url"]


# ── overlay 유틸 ──────────────────────────────────────────────────────────────


def _overlay_fill(
    base_rgb: np.ndarray,
    mask: np.ndarray,
    color: tuple[int, int, int],
    alpha: float,
) -> np.ndarray:
    if not mask.any():
        return base_rgb.copy()
    out_f = base_rgb.astype(np.float32)
    color_arr = np.array(color, dtype=np.float32)
    out_f[mask] = out_f[mask] * (1 - alpha) + color_arr * alpha
    return np.clip(out_f, 0, 255).astype(np.uint8)


def _outline_ring(mask: np.ndarray, thickness: int) -> np.ndarray:
    up = np.roll(mask, -thickness, axis=0)
    down = np.roll(mask, thickness, axis=0)
    left = np.roll(mask, -thickness, axis=1)
    right = np.roll(mask, thickness, axis=1)
    dilated = mask | up | down | left | right
    return dilated & ~mask


def _apply_part_overlay(
    base_rgb: np.ndarray,
    mask: np.ndarray,
    color: tuple[int, int, int],
) -> np.ndarray:
    filled = _overlay_fill(base_rgb, mask, color, OVERLAY_ALPHA)
    ring = _outline_ring(mask, OUTLINE_THICKNESS)
    filled[ring] = np.array(color, dtype=np.uint8)
    return filled


def _save_jpeg(rgb: np.ndarray) -> bytes:
    img = Image.fromarray(rgb, mode="RGB")
    w, h = img.size
    scale = min(1.0, _JPEG_MAX_SIDE / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


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


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=60.0)
        resp.raise_for_status()
        return resp.content


def _bool_mask_from_png(png_bytes: bytes, target_size: tuple[int, int]) -> np.ndarray:
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    if img.size != target_size:
        img = img.resize(target_size, Image.NEAREST)
    return np.asarray(img, dtype=np.uint8) >= 128


# ── 메인 ──────────────────────────────────────────────────────────────────────


async def _run() -> dict:
    if not os.getenv("FAL_KEY", "").strip():
        print(json.dumps({"error": "FAL_KEY 환경변수 미설정"}), flush=True)
        sys.exit(2)
    for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"):
        if not os.getenv(k):
            print(json.dumps({"error": f"{k} 환경변수 미설정"}), flush=True)
            sys.exit(2)
    if not IMAGE_PATH.exists():
        print(json.dumps({"error": f"이미지 없음: {IMAGE_PATH}"}), flush=True)
        sys.exit(2)
    _configure_cloudinary()

    # 원본 로드
    orig_bytes = IMAGE_PATH.read_bytes()
    orig = Image.open(io.BytesIO(orig_bytes)).convert("RGB")
    img_w, img_h = orig.size
    base_rgb = np.asarray(orig, dtype=np.uint8)
    logger.info("원본: %s %dx%d", IMAGE_PATH.name, img_w, img_h)

    # Cloudinary 업로드 (fal 는 URL 입력)
    image_url = _upload_bytes(orig_bytes, folder="grooming-style/probe")
    logger.info("원본 업로드: %s", image_url)

    # 앵커 마스크 호출
    try:
        result = await get_anchor_masks(
            image_url, (img_w, img_h), use_convex_hull=USE_CONVEX_HULL
        )
    except QualityFailure as exc:
        payload = {"error": "QualityFailure", "reason": str(exc)}
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        sys.exit(3)

    # pre-cleanup nose (raw mask URL 재다운로드)
    raw_nose_url = result.diagnostics["raw_mask_urls"]["nose"]
    raw_nose_bytes = await _download(raw_nose_url)
    raw_nose_mask = _bool_mask_from_png(raw_nose_bytes, (img_w, img_h))

    # overlay 1 — pre-cleanup nose
    pre_rgb = _apply_part_overlay(base_rgb, raw_nose_mask, PART_COLORS["nose"])
    pre_url = _upload_bytes(
        _save_jpeg(pre_rgb),
        folder="grooming-style/probe/evf-sam-geometry",
        tag="nose-pre-cleanup",
    )
    logger.info("[overlay] nose_pre_cleanup: %s", pre_url)

    # overlay 2 — post-cleanup nose
    post_nose_mask = result.parts["nose"].mask
    post_rgb = _apply_part_overlay(base_rgb, post_nose_mask, PART_COLORS["nose"])
    post_url = _upload_bytes(
        _save_jpeg(post_rgb),
        folder="grooming-style/probe/evf-sam-geometry",
        tag="nose-post-cleanup",
    )
    logger.info("[overlay] nose_post_cleanup: %s", post_url)

    # overlay 3 — combined (최종 라벨 기준)
    combined_rgb = base_rgb.copy()
    for name in ("left_eye", "right_eye", "nose"):
        if name not in result.parts:
            continue
        combined_rgb = _apply_part_overlay(
            combined_rgb, result.parts[name].mask, PART_COLORS[name]
        )
    combined_url = _upload_bytes(
        _save_jpeg(combined_rgb),
        folder="grooming-style/probe/evf-sam-geometry",
        tag="combined-aligned",
    )
    logger.info("[overlay] combined_aligned: %s", combined_url)

    # 수치
    parts_stats = {
        name: _mask_stats(part.mask, img_w, img_h)
        for name, part in result.parts.items()
    }
    nose_raw_stats = _mask_stats(raw_nose_mask, img_w, img_h)

    return {
        "image_size": [img_w, img_h],
        "image_url": image_url,
        "mode": result.mode,
        "position_gate_ok": result.position_gate_ok,
        "label_swapped": result.label_swapped,
        "use_convex_hull": USE_CONVEX_HULL,
        "overlay_urls": {
            "nose_pre_cleanup": pre_url,
            "nose_post_cleanup": post_url,
            "combined_aligned": combined_url,
        },
        "parts_final": parts_stats,
        "nose_raw": nose_raw_stats,
        "diagnostics": {
            "raw_mask_urls": result.diagnostics.get("raw_mask_urls"),
            "nose_cleanup": result.diagnostics.get("nose_cleanup"),
            "eye_label_mapping": result.diagnostics.get("eye_label_mapping"),
            "position_gate": result.diagnostics.get("position_gate"),
        },
        "legend": {"left_eye": "red", "right_eye": "green", "nose": "blue"},
    }


def main() -> None:
    payload = asyncio.run(_run())
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
