"""
EVF-SAM2 text-grounded segmentation probe — Gemini geometry 완전 제거 검증.

목적:
 - `fal-ai/evf-sam` 의 text prompt 만으로 left_eye / right_eye / nose mask 를 뽑고,
   동일 입력 이미지에 대해 N_REPEATS 회 반복했을 때 mask 중심점(cx, cy)이
   Gemini VLM 보다 수렴하는지 baseline 과 같은 축(원본 short_side)으로 비교한다.

왜 seed 에 Gemini point/bbox 를 쓰지 않는가:
 - Phase 32~33 에서 Gemini VLM 의 regime-switching 이 공간 localization 이 아닌
   의미 수준 오류임이 확인되었다. 틀린 seed 를 주면 SAM2 가 틀린 mask 를
   결정론적으로 고정시킨다. 따라서 본 스파이크는 "seed source 가 Gemini 와
   독립해야 한다" 는 제약을 엄수한다.

절차:
 1) 테스트 이미지 → Cloudinary 업로드 (fal 는 URL 입력)
 2) 각 파트별 text prompt 로 evf-sam 호출, semantic_type=true, mask_only=true
    - left_eye : "left eye of the dog"
    - right_eye: "right eye of the dog"
    - nose     : "nose of the dog"
 3) 각 파트 N_REPEATS(=3) 회 호출, mask PNG 다운로드
 4) mask 의 foreground(픽셀 >= 128) 중심점(cx, cy) 계산
 5) 원본 short_side 로 정규화한 cx/cy std_ratio 산출
 6) VERDICT_PARTS 중 max(cx_std_ratio, cy_std_ratio) 최댓값 기준 verdict 판정

verdict (baseline 과 동일 임계):
 - stable      ≤ 0.010
 - borderline  ≤ 0.025
 - unstable    > 0.025

추천:
 - stable/borderline → "proceed_with_evf_sam2"
 - unstable          → "escalate_to_auto_segment_or_fine_tune"

공간 배치 타당성(추가 판정):
 - 원본 픽셀 기준 nose.cy > left_eye.cy, nose.cy > right_eye.cy (눈이 코 위)
 - left_eye 와 right_eye 의 x 순서 일관성 (좌우 스왑 없음)

stdout 에 JSON 한 줄 출력. raw 로그는 stderr.

실행:
  cd backend
  source venv/bin/activate
  python tests/run_evf_sam2_probe.py

전제:
 - 환경변수 FAL_KEY, CLOUDINARY_* 필요
 - 테스트 이미지 고정: /Users/soyeon/Downloads/IMG_7641.jpg
 - 재시도 루프 없음 — API 실패는 그대로 기록
 - 총 fal API 호출: 3 parts × 3 repeats = 9 회
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import statistics
import sys
from pathlib import Path

import cloudinary
import cloudinary.uploader
import fal_client
import httpx
import numpy as np
import pillow_heif

pillow_heif.register_heif_opener()

from dotenv import load_dotenv
from PIL import Image

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

load_dotenv(_BACKEND_ROOT / ".env")

# ── 설정 ──────────────────────────────────────────────────────────────────────

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")

# 파트별 text prompt. 프롬프트는 명시적으로 좌/우 방향을 적어서 좌우 스왑 오류를 최소화.
PART_PROMPTS: dict[str, str] = {
    "left_eye": "left eye of the dog",
    "right_eye": "right eye of the dog",
    "nose": "nose of the dog",
}
VERDICT_PARTS = ("left_eye", "right_eye", "nose")

N_REPEATS = 3

STABLE_THRESHOLD = 0.010
BORDERLINE_THRESHOLD = 0.025

# baseline (run_geometry_variance.py 실측, 전체 이미지 단일 호출 N=5 기준)
BASELINE_GEMINI = {
    "max_std_ratio": 0.068,
    "basis_part": "nose.cy",
    "verdict": "unstable",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("run_evf_sam2_probe")


# ── Cloudinary 설정 ───────────────────────────────────────────────────────────


def _configure_cloudinary() -> None:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )


def _upload_image_bytes(image_bytes: bytes, folder: str = "grooming-style/probe") -> str:
    result = cloudinary.uploader.upload(
        image_bytes,
        folder=folder,
        resource_type="image",
        format="jpg",
    )
    return result["secure_url"]


# ── 유틸 ──────────────────────────────────────────────────────────────────────


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def _verdict(max_ratio: float) -> str:
    if max_ratio <= STABLE_THRESHOLD:
        return "stable"
    if max_ratio <= BORDERLINE_THRESHOLD:
        return "borderline"
    return "unstable"


async def _download_png(url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=60.0)
        resp.raise_for_status()
        return resp.content


def _mask_centroid(mask_bytes: bytes, orig_size: tuple[int, int]) -> tuple[float, float, int] | None:
    """mask PNG bytes 에서 foreground(>=128) 중심점(cx, cy)과 면적을 돌려준다.

    fal evf-sam 의 mask 는 입력과 동일 해상도일 수도 있고 내부 리샘플 결과일 수도 있다.
    mask 크기와 orig_size 가 다르면 orig 좌표계로 스케일링한다.

    foreground 픽셀이 0 개면 None.
    """
    img = Image.open(io.BytesIO(mask_bytes)).convert("L")
    mw, mh = img.size
    arr = np.asarray(img, dtype=np.uint8)
    fg = arr >= 128
    n = int(fg.sum())
    if n == 0:
        return None

    ys, xs = np.nonzero(fg)
    cx_m = float(xs.mean())
    cy_m = float(ys.mean())

    ow, oh = orig_size
    if (mw, mh) != (ow, oh):
        cx = cx_m * (ow / mw)
        cy = cy_m * (oh / mh)
    else:
        cx, cy = cx_m, cy_m
    return (cx, cy, n)


async def _one_evf_sam_call(
    image_url: str,
    prompt: str,
    orig_size: tuple[int, int],
    tag: str,
) -> dict | None:
    """1회 evf-sam 호출 → mask 다운로드 → 중심점/면적 계산."""
    logger.info("[%s] evf-sam 호출: prompt=%r", tag, prompt)
    try:
        result = await asyncio.to_thread(
            fal_client.run,
            "fal-ai/evf-sam",
            arguments={
                "prompt": prompt,
                "image_url": image_url,
                "semantic_type": True,
                "mask_only": True,
                "fill_holes": True,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] evf-sam 호출 예외: %s", tag, exc)
        return None

    # 출력 스키마: {"image": {"url": ..., ...}}
    image_obj = result.get("image") if isinstance(result, dict) else None
    if not image_obj or "url" not in image_obj:
        logger.warning("[%s] evf-sam 응답에 image.url 없음: %s", tag, result)
        return None

    mask_url = image_obj["url"]
    try:
        mask_bytes = await _download_png(mask_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] mask 다운로드 실패: %s", tag, exc)
        return None

    centroid = _mask_centroid(mask_bytes, orig_size)
    if centroid is None:
        logger.warning("[%s] mask foreground 0 — 탐지 실패", tag)
        return {"mask_url": mask_url, "cx": None, "cy": None, "area_px": 0}

    cx, cy, area = centroid
    logger.info(
        "[%s] cx=%.1f cy=%.1f area=%d px (mask_url=%s)",
        tag, cx, cy, area, mask_url,
    )
    return {"mask_url": mask_url, "cx": cx, "cy": cy, "area_px": area}


def _evaluate(
    per_part_calls: dict[str, list[dict]],
    short_side: int,
) -> dict:
    parts_metrics: dict[str, dict] = {}
    for name, calls in per_part_calls.items():
        cxs = [c["cx"] for c in calls if c is not None and c.get("cx") is not None]
        cys = [c["cy"] for c in calls if c is not None and c.get("cy") is not None]
        n_hits = len(cxs)
        cx_std = _std(cxs)
        cy_std = _std(cys)
        parts_metrics[name] = {
            "n_hits": n_hits,
            "cx_mean": (sum(cxs) / n_hits) if n_hits else None,
            "cy_mean": (sum(cys) / n_hits) if n_hits else None,
            "cx_std_ratio": (cx_std / short_side) if short_side > 0 else 0.0,
            "cy_std_ratio": (cy_std / short_side) if short_side > 0 else 0.0,
        }

    # verdict
    candidates: list[tuple[float, str]] = []
    for name in VERDICT_PARTS:
        m = parts_metrics.get(name, {})
        if not m or m.get("n_hits", 0) < 2:
            continue
        worst = max(m["cx_std_ratio"], m["cy_std_ratio"])
        candidates.append((worst, name))

    if not candidates:
        max_ratio = float("inf")
        basis_part = "no_verdict_parts_detected"
        verdict = "unstable"
    else:
        max_ratio, basis_part = max(candidates, key=lambda x: x[0])
        verdict = _verdict(max_ratio)

    # 공간 배치 타당성 — mean 좌표 기준
    spatial_checks: dict = {}
    le = parts_metrics.get("left_eye", {})
    re = parts_metrics.get("right_eye", {})
    nose = parts_metrics.get("nose", {})
    if le.get("cy_mean") is not None and nose.get("cy_mean") is not None:
        spatial_checks["nose_below_left_eye"] = nose["cy_mean"] > le["cy_mean"]
    if re.get("cy_mean") is not None and nose.get("cy_mean") is not None:
        spatial_checks["nose_below_right_eye"] = nose["cy_mean"] > re["cy_mean"]
    if le.get("cx_mean") is not None and re.get("cx_mean") is not None:
        # 일반적으로 사진 기준 "left eye of the dog" 는 사진 우측에 위치 (= cx 더 큼)
        spatial_checks["left_eye_cx_gt_right_eye_cx"] = le["cx_mean"] > re["cx_mean"]

    return {
        "parts": parts_metrics,
        "max_std_ratio": max_ratio,
        "basis_part": basis_part,
        "verdict": verdict,
        "spatial_checks": spatial_checks,
    }


def _pick_recommendation(verdict: str, spatial_checks: dict) -> str:
    all_spatial_ok = all(spatial_checks.values()) if spatial_checks else False
    if verdict in ("stable", "borderline") and all_spatial_ok:
        return "proceed_with_evf_sam2"
    return "escalate_to_auto_segment_or_fine_tune"


# ── 메인 ──────────────────────────────────────────────────────────────────────


async def _run() -> dict:
    fal_key = os.getenv("FAL_KEY", "").strip()
    if not fal_key:
        print(json.dumps({"error": "FAL_KEY 환경변수 미설정"}), flush=True)
        sys.exit(2)
    if not IMAGE_PATH.exists():
        print(json.dumps({"error": f"이미지 파일 없음: {IMAGE_PATH}"}), flush=True)
        sys.exit(2)
    for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"):
        if not os.getenv(k):
            print(json.dumps({"error": f"{k} 환경변수 미설정"}), flush=True)
            sys.exit(2)
    _configure_cloudinary()

    image_bytes = IMAGE_PATH.read_bytes()
    with Image.open(io.BytesIO(image_bytes)) as img:
        img_w, img_h = img.size
    short_side = min(img_w, img_h)
    logger.info(
        "이미지: %s size=(%d, %d) short_side=%d",
        IMAGE_PATH.name, img_w, img_h, short_side,
    )

    # Cloudinary 업로드 (동기 API 이지만 스파이크이므로 그대로 사용)
    logger.info("Cloudinary 업로드 중...")
    image_url = _upload_image_bytes(image_bytes)
    logger.info("업로드 완료: %s", image_url)

    per_part_calls: dict[str, list[dict]] = {name: [] for name in PART_PROMPTS}

    for name, prompt in PART_PROMPTS.items():
        for rep in range(1, N_REPEATS + 1):
            tag = f"{name} rep={rep}/{N_REPEATS}"
            call = await _one_evf_sam_call(
                image_url, prompt, (img_w, img_h), tag
            )
            per_part_calls[name].append(call if call else {"cx": None, "cy": None, "area_px": 0})

    ev = _evaluate(per_part_calls, short_side=short_side)
    recommendation = _pick_recommendation(ev["verdict"], ev["spatial_checks"])

    return {
        "image_size": [img_w, img_h],
        "short_side": short_side,
        "image_url": image_url,
        "prompts": PART_PROMPTS,
        "n_repeats": N_REPEATS,
        "total_fal_calls": len(PART_PROMPTS) * N_REPEATS,
        "baseline_gemini": BASELINE_GEMINI,
        "per_part_calls": per_part_calls,
        "evaluation": ev,
        "recommendation": recommendation,
    }


def main() -> None:
    result = asyncio.run(_run())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
