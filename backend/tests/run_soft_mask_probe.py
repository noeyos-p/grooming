"""
FLUX.1-fill soft-mask probe — grayscale 중간값 마스크가 soft-band 전이 신호로
실제 작동하는지 정량 비교하는 선행 실험 스크립트.

조건 A (binary mask):
  - head_bbox 내부 255 (인페인팅 대상), hard 파트(left_eye/right_eye/nose) 주변은
    r2 dilation만큼 edit 영역에서 차감, hard 내부 자체는 0 (보존)
조건 B (gradient mask):
  - A와 동일하게 시작
  - hard bbox 외곽 r1 폭 링 구간에 distanceTransform 기반 선형 그라디언트 주입
    (hard 경계 = 0, r1 링 시작점 = 255에 가까움)

지표:
  - Boundary gradient magnitude: hard bbox 외곽 r1 링 내의 |Sobel(dx)+Sobel(dy)| 평균
  - Seam score: hard bbox 경계 1~3px 바깥 링의 |orig_gray - result_gray| 평균

Pass 조건 (정량):
  - B_grad ≤ 0.7 * A_grad  AND  B_seam ≤ 0.8 * A_seam

Usage:
    cd backend
    source venv/bin/activate
    python tests/run_soft_mask_probe.py
"""

import asyncio
import io
import json
import os
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_BACKEND_DIR / ".env")

import cv2
import numpy as np
import pillow_heif
pillow_heif.register_heif_opener()

import cloudinary
import cloudinary.uploader
import fal_client  # noqa: F401  # FAL_KEY 환경변수 활용
from google import genai
from PIL import Image

from services.inpaint_pipeline import (
    _detect_full_head_bbox,
    _resize_for_flux,
    _resize_mask_for_flux,
    _run_flux_fill,
)
from services.gemini_pipeline import (
    _detect_face_parts_bboxes,
    _extract_dominant_fur_colors,
)
from services.style_prompts import get_prompt


IMAGE_PATH = Path.home() / "Downloads" / "IMG_7641.jpg"
BREED_ID = "maltese"
STYLE_ID = "teddy_cut"
OUT_DIR = _BACKEND_DIR / "tests" / "_out"
HARD_PART_NAMES = ("left_eye", "right_eye", "nose")

PASS_GRAD_RATIO = 0.7
PASS_SEAM_RATIO = 0.8


def _require_env() -> None:
    """필수 환경변수 확인 — 누락 시 명확한 에러로 종료."""
    required = [
        "FAL_KEY",
        "GOOGLE_API_KEY",
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"[probe] 필수 환경변수 누락: {missing}. backend/.env 를 확인하세요."
        )
    cloudinary.config(
        cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
        api_key=os.environ["CLOUDINARY_API_KEY"],
        api_secret=os.environ["CLOUDINARY_API_SECRET"],
    )


def _clip_bbox(bbox: dict, w: int, h: int) -> tuple[int, int, int, int]:
    """bbox를 이미지 경계로 clip 후 (x1, y1, x2, y2) 반환."""
    x1 = max(0, min(w, int(bbox["xmin"])))
    y1 = max(0, min(h, int(bbox["ymin"])))
    x2 = max(0, min(w, int(bbox["xmax"])))
    y2 = max(0, min(h, int(bbox["ymax"])))
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return x1, y1, x2, y2


def _compute_radii(part: dict) -> tuple[int, int]:
    """hard 파트 bbox 크기에서 r1(soft band 폭), r2(edit 차감 폭) 계산."""
    w = part["xmax"] - part["xmin"]
    h = part["ymax"] - part["ymin"]
    min_side = max(1, min(w, h))
    r1 = max(3, min(12, round(min_side * 0.15)))
    r2 = max(8, min(28, round(min_side * 0.30)))
    return int(r1), int(r2)


def _build_masks(
    img_w: int,
    img_h: int,
    head_bbox: dict,
    hard_parts: list[dict],
) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """조건 A(binary)와 B(gradient) 마스크를 numpy uint8 L 배열로 생성한다.

    Returns:
        (mask_A, mask_B, radii_meta)
        radii_meta: 각 hard part의 (name, r1, r2, bbox) 기록용
    """
    # head 내부 edit 영역 (0/255 binary)
    edit_layer = np.zeros((img_h, img_w), dtype=np.uint8)
    hx1, hy1, hx2, hy2 = _clip_bbox(head_bbox, img_w, img_h)
    edit_layer[hy1:hy2, hx1:hx2] = 255

    # hard 주변 차감 영역 (255 = "edit에서 제외하고 hard 보존 유지")
    subtract_layer = np.zeros((img_h, img_w), dtype=np.uint8)

    radii_meta: list[dict] = []
    hard_bboxes: list[tuple[int, int, int, int]] = []

    for part in hard_parts:
        r1, r2 = _compute_radii(part)
        px1, py1, px2, py2 = _clip_bbox(part, img_w, img_h)
        # r2 dilation 영역: edit에서 차감
        sx1 = max(0, px1 - r2)
        sy1 = max(0, py1 - r2)
        sx2 = min(img_w, px2 + r2)
        sy2 = min(img_h, py2 + r2)
        subtract_layer[sy1:sy2, sx1:sx2] = 255
        hard_bboxes.append((px1, py1, px2, py2))
        radii_meta.append({
            "name": part["name"], "r1": r1, "r2": r2,
            "bbox": [px1, py1, px2, py2],
        })

    # 조건 A: edit 영역 - subtract 영역 (hard 내부 포함해 0) → binary
    mask_A = np.where(
        (edit_layer == 255) & (subtract_layer == 0), 255, 0
    ).astype(np.uint8)

    # 조건 B: A와 동일하게 시작 후 soft band 주입
    mask_B = mask_A.copy()

    # 각 hard bbox에 대해 r1 폭의 링에서 선형 그라디언트 (hard 경계=0, r1=255쪽) 주입
    # 접근: hard bbox 내부만 0인 이진 맵에서 distanceTransform → dist ∈ (0, r1]
    #       soft_band = 255 * (d / r1), hard 내부(d=0)는 그대로 0
    for (px1, py1, px2, py2), meta in zip(hard_bboxes, radii_meta):
        r1 = meta["r1"]
        if r1 <= 0:
            continue
        # 주변 윈도우 (hard bbox + r1*2 여유)
        pad = r1 + 2
        wx1 = max(0, px1 - pad)
        wy1 = max(0, py1 - pad)
        wx2 = min(img_w, px2 + pad)
        wy2 = min(img_h, py2 + pad)
        win_w = wx2 - wx1
        win_h = wy2 - wy1
        if win_w <= 0 or win_h <= 0:
            continue

        # 로컬 좌표
        lx1 = px1 - wx1
        ly1 = py1 - wy1
        lx2 = px2 - wx1
        ly2 = py2 - wy1

        # hard 내부를 0, 바깥을 1로 (distanceTransform: 0에서 바깥 픽셀까지 거리)
        binary_local = np.ones((win_h, win_w), dtype=np.uint8)
        binary_local[ly1:ly2, lx1:lx2] = 0
        dist = cv2.distanceTransform(binary_local, cv2.DIST_L2, 3)

        # 링 영역: 0 < d ≤ r1
        ring_mask = (dist > 0) & (dist <= r1)
        # 선형 그라디언트: hard 경계(d→0)에서 0, r1 끝에서 255에 근접
        grad_vals = np.clip(255.0 * (dist / float(r1)), 0, 255).astype(np.uint8)

        # mask_B 윈도우 패치 적용: 링 안에서만 값 덮어쓰기 (원래 A에서 0이던 곳)
        window_B = mask_B[wy1:wy2, wx1:wx2]
        # 링은 subtract_layer에 의해 A에서 0인 영역과 겹침 → 그 영역만 grad_vals로 덮어쓰기
        # A에서 이미 255(머리 edit 영역)인 곳은 건드리지 않음
        target = ring_mask & (window_B == 0)
        window_B[target] = grad_vals[target]
        mask_B[wy1:wy2, wx1:wx2] = window_B

    return mask_A, mask_B, radii_meta


def _mask_to_png_bytes(mask: np.ndarray) -> bytes:
    img = Image.fromarray(mask, mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_cloudinary(data: bytes, folder: str, fmt: str) -> str:
    res = cloudinary.uploader.upload(
        data,
        folder=folder,
        resource_type="image",
        format=fmt,
    )
    return res["secure_url"]


def _build_flux_prompt(extracted_colors: str, base_prompt: str) -> str:
    """inpaint_pipeline.py:511-521 스타일의 color clause + base_prompt."""
    if extracted_colors:
        color_clause = (
            f"The dog's fur color is exactly {extracted_colors}. "
            "Preserve this EXACT fur color. Do NOT change color under any circumstances."
        )
    else:
        color_clause = (
            "Preserve the dog's exact original fur color. "
            "Do NOT change the fur color."
        )
    return f"{color_clause} {base_prompt}"


def _resize_to(image_bytes: bytes, target: tuple[int, int]) -> np.ndarray:
    """image_bytes를 target (W, H) 크기의 RGB numpy 배열로 반환."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if img.size != target:
        img = img.resize(target, Image.LANCZOS)
    return np.array(img)


def _boundary_gradient_score(
    result_arr: np.ndarray,
    hard_bboxes: list[tuple[int, int, int, int]],
    r1_list: list[int],
) -> float:
    """hard bbox 외곽 r1 링 영역에서 |Sobel(dx)| + |Sobel(dy)| 평균."""
    h, w, _ = result_arr.shape
    gray = cv2.cvtColor(result_arr, cv2.COLOR_RGB2GRAY).astype(np.float32)
    sx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = np.abs(sx) + np.abs(sy)

    ring_mask = np.zeros((h, w), dtype=bool)
    for (px1, py1, px2, py2), r1 in zip(hard_bboxes, r1_list):
        if r1 <= 0:
            continue
        outer_x1 = max(0, px1 - r1)
        outer_y1 = max(0, py1 - r1)
        outer_x2 = min(w, px2 + r1)
        outer_y2 = min(h, py2 + r1)
        outer = np.zeros((h, w), dtype=bool)
        outer[outer_y1:outer_y2, outer_x1:outer_x2] = True
        inner = np.zeros((h, w), dtype=bool)
        inner[py1:py2, px1:px2] = True
        ring_mask |= (outer & ~inner)

    if not ring_mask.any():
        return float("nan")
    return float(grad_mag[ring_mask].mean())


def _seam_score(
    original_arr: np.ndarray,
    result_arr: np.ndarray,
    hard_bboxes: list[tuple[int, int, int, int]],
) -> float:
    """hard bbox 경계에서 정확히 1~3px 바깥 링에서 |orig_gray - result_gray| 평균."""
    assert original_arr.shape == result_arr.shape
    h, w, _ = original_arr.shape
    og = cv2.cvtColor(original_arr, cv2.COLOR_RGB2GRAY).astype(np.float32)
    rg = cv2.cvtColor(result_arr, cv2.COLOR_RGB2GRAY).astype(np.float32)
    diff = np.abs(og - rg)

    ring_mask = np.zeros((h, w), dtype=bool)
    for (px1, py1, px2, py2) in hard_bboxes:
        outer3_x1 = max(0, px1 - 3)
        outer3_y1 = max(0, py1 - 3)
        outer3_x2 = min(w, px2 + 3)
        outer3_y2 = min(h, py2 + 3)
        outer1_x1 = max(0, px1 - 1)
        outer1_y1 = max(0, py1 - 1)
        outer1_x2 = min(w, px2 + 1)
        outer1_y2 = min(h, py2 + 1)
        outer3 = np.zeros((h, w), dtype=bool)
        outer3[outer3_y1:outer3_y2, outer3_x1:outer3_x2] = True
        outer1 = np.zeros((h, w), dtype=bool)
        outer1[outer1_y1:outer1_y2, outer1_x1:outer1_x2] = True
        # 1~3px 바깥 링 = outer3 내부 - outer1 내부 (1px 바깥까지 포함한 영역 제외)
        ring_mask |= (outer3 & ~outer1)

    if not ring_mask.any():
        return float("nan")
    return float(diff[ring_mask].mean())


def _save_diff_heatmap(
    original_arr: np.ndarray,
    result_arr: np.ndarray,
    out_path: Path,
) -> None:
    """원본과 결과의 채널합 절대차를 colormap으로 시각화해 PNG로 저장."""
    assert original_arr.shape == result_arr.shape
    diff = np.abs(original_arr.astype(np.int16) - result_arr.astype(np.int16))
    diff_sum = diff.sum(axis=2)  # 0..765
    # 0..255로 정규화 (상한을 실측 max로 잡되 0이면 0)
    dmax = float(diff_sum.max()) if diff_sum.size else 0.0
    if dmax > 0:
        norm = (diff_sum / dmax * 255.0).clip(0, 255).astype(np.uint8)
    else:
        norm = np.zeros_like(diff_sum, dtype=np.uint8)
    colored = cv2.applyColorMap(norm, cv2.COLORMAP_JET)
    # cv2는 BGR 저장
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), colored)


def _save_image(arr: np.ndarray, out_path: Path) -> None:
    """RGB numpy 배열을 PNG로 저장."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(out_path, format="PNG")


async def main() -> dict:
    _require_env()

    if not IMAGE_PATH.exists():
        raise SystemExit(f"[probe] 테스트 이미지 없음: {IMAGE_PATH}")

    prompt_data = get_prompt(BREED_ID, STYLE_ID)
    if prompt_data is None:
        raise SystemExit(f"[probe] 유효하지 않은 breed/style: {BREED_ID}/{STYLE_ID}")

    image_bytes = IMAGE_PATH.read_bytes()
    img_orig = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img_w, img_h = img_orig.size
    # JPEG로 재직렬화 (HEIC 등 대비)
    buf = io.BytesIO()
    img_orig.save(buf, format="JPEG", quality=95)
    image_bytes = buf.getvalue()

    print(
        f"[probe] 이미지 로드: {IMAGE_PATH} ({img_w}x{img_h})",
        file=sys.stderr,
    )

    gemini_client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    head_bbox, face_parts = await asyncio.gather(
        _detect_full_head_bbox(image_bytes, gemini_client),
        _detect_face_parts_bboxes(image_bytes, gemini_client),
    )
    if head_bbox is None:
        raise SystemExit("[probe] head bbox 탐지 실패")
    if not face_parts:
        raise SystemExit("[probe] face_parts 탐지 실패")

    hard_parts = [p for p in face_parts if p["name"] in HARD_PART_NAMES]
    if not hard_parts:
        raise SystemExit(
            "[probe] hard 파트(left_eye/right_eye/nose) 탐지 실패"
        )

    print(
        f"[probe] head_bbox={head_bbox}, hard_parts={[p['name'] for p in hard_parts]}",
        file=sys.stderr,
    )

    mask_A, mask_B, radii_meta = _build_masks(img_w, img_h, head_bbox, hard_parts)

    # 프롬프트 구성 (동일 프롬프트 2회)
    extracted_colors = _extract_dominant_fur_colors(image_bytes)
    flux_prompt = _build_flux_prompt(extracted_colors, prompt_data["prompt"])

    # Cloudinary 업로드: 원본 이미지 (FLUX 실행용)
    original_url = _upload_cloudinary(image_bytes, "grooming-temp/probe", "jpg")

    # FLUX 전송 크기로 리사이즈
    flux_image_bytes = _resize_for_flux(image_bytes)
    flux_image_size = Image.open(io.BytesIO(flux_image_bytes)).size

    mask_A_bytes = _mask_to_png_bytes(mask_A)
    mask_B_bytes = _mask_to_png_bytes(mask_B)
    flux_mask_A_bytes = _resize_mask_for_flux(mask_A_bytes, flux_image_size)
    flux_mask_B_bytes = _resize_mask_for_flux(mask_B_bytes, flux_image_size)

    flux_image_url = _upload_cloudinary(flux_image_bytes, "grooming-temp/probe", "jpg")
    flux_mask_A_url = _upload_cloudinary(flux_mask_A_bytes, "grooming-temp/probe", "png")
    flux_mask_B_url = _upload_cloudinary(flux_mask_B_bytes, "grooming-temp/probe", "png")

    print("[probe] FLUX A 호출 시작", file=sys.stderr)
    result_A_bytes = await _run_flux_fill(
        flux_image_url, flux_mask_A_url, flux_prompt,
        negative_prompt=prompt_data.get("negative_prompt", ""),
    )
    print("[probe] FLUX B 호출 시작", file=sys.stderr)
    result_B_bytes = await _run_flux_fill(
        flux_image_url, flux_mask_B_url, flux_prompt,
        negative_prompt=prompt_data.get("negative_prompt", ""),
    )

    # 원본 해상도로 리사이즈한 RGB numpy
    original_arr = np.array(img_orig)
    result_A_arr = _resize_to(result_A_bytes, (img_w, img_h))
    result_B_arr = _resize_to(result_B_bytes, (img_w, img_h))

    # hard bbox 목록 + r1 목록
    hard_bboxes = [tuple(m["bbox"]) for m in radii_meta]
    r1_list = [m["r1"] for m in radii_meta]

    grad_A = _boundary_gradient_score(result_A_arr, hard_bboxes, r1_list)
    grad_B = _boundary_gradient_score(result_B_arr, hard_bboxes, r1_list)
    seam_A = _seam_score(original_arr, result_A_arr, hard_bboxes)
    seam_B = _seam_score(original_arr, result_B_arr, hard_bboxes)

    # 로컬 저장 (결과 이미지 + diff 히트맵)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img_A_path = OUT_DIR / "probe_result_A.png"
    img_B_path = OUT_DIR / "probe_result_B.png"
    heat_A_path = OUT_DIR / "probe_heatmap_A.png"
    heat_B_path = OUT_DIR / "probe_heatmap_B.png"
    mask_A_path = OUT_DIR / "probe_mask_A.png"
    mask_B_path = OUT_DIR / "probe_mask_B.png"

    _save_image(result_A_arr, img_A_path)
    _save_image(result_B_arr, img_B_path)
    _save_diff_heatmap(original_arr, result_A_arr, heat_A_path)
    _save_diff_heatmap(original_arr, result_B_arr, heat_B_path)
    Image.fromarray(mask_A, mode="L").save(mask_A_path, format="PNG")
    Image.fromarray(mask_B, mode="L").save(mask_B_path, format="PNG")

    # 원본 해상도 결과를 Cloudinary에 업로드
    def _to_jpeg_bytes(arr: np.ndarray) -> bytes:
        b = io.BytesIO()
        Image.fromarray(arr).save(b, format="JPEG", quality=95)
        return b.getvalue()

    result_A_url = _upload_cloudinary(
        _to_jpeg_bytes(result_A_arr), "grooming-results/probe", "jpg"
    )
    result_B_url = _upload_cloudinary(
        _to_jpeg_bytes(result_B_arr), "grooming-results/probe", "jpg"
    )

    ratio_grad = (grad_B / grad_A) if grad_A and grad_A > 0 else float("nan")
    ratio_seam = (seam_B / seam_A) if seam_A and seam_A > 0 else float("nan")

    passed = (
        np.isfinite(grad_A) and np.isfinite(grad_B)
        and np.isfinite(seam_A) and np.isfinite(seam_B)
        and (grad_B <= PASS_GRAD_RATIO * grad_A)
        and (seam_B <= PASS_SEAM_RATIO * seam_A)
    )

    report = {
        "A": {
            "result_url": result_A_url,
            "boundary_gradient": round(float(grad_A), 4),
            "seam_score": round(float(seam_A), 4),
        },
        "B": {
            "result_url": result_B_url,
            "boundary_gradient": round(float(grad_B), 4),
            "seam_score": round(float(seam_B), 4),
        },
        "ratios": {
            "boundary_gradient_B_over_A": round(float(ratio_grad), 4),
            "seam_score_B_over_A": round(float(ratio_seam), 4),
        },
        "passed_quantitative": bool(passed),
        "local_heatmap_paths": [str(heat_A_path), str(heat_B_path)],
        "breed_style_used": f"{BREED_ID}/{STYLE_ID}",
        "image_size": [img_w, img_h],
        "original_url": original_url,
        "mask_urls": {"A": flux_mask_A_url, "B": flux_mask_B_url},
        "local_result_paths": [str(img_A_path), str(img_B_path)],
        "local_mask_paths": [str(mask_A_path), str(mask_B_path)],
        "radii_meta": radii_meta,
    }
    return report


if __name__ == "__main__":
    try:
        report = asyncio.run(main())
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - 진단용
        import traceback
        traceback.print_exc()
        sys.exit(1)
    # 표준출력에 JSON 한 줄
    print(json.dumps(report, ensure_ascii=False))
