"""
2-stage crop Gemini probe — head crop 후 face parts 분산 재측정.

가설: Gemini VLM의 face parts 분산은 "전체 이미지에서 어디를 볼지" 자체의
regime-switching 때문. head_bbox 로 먼저 좁힌 crop 에서 face parts 를 찾으면
분산이 줄어들 수 있다.

비교 baseline (run_geometry_variance.py 결과, 전체 이미지 단일 호출 5회):
 - nose.cy_std_ratio = 0.068 (unstable)
 - max_std_ratio = 0.068, basis_part = nose
 - verdict = unstable

본 스파이크:
 1) `_detect_full_head_bbox()` 1회 → head_bbox 확정 (crop 좌표 고정)
 2) head_bbox 에 CROP_PADDING_RATIO 여유를 주어 crop → PNG bytes
 3) crop bytes 로 `_detect_face_parts_bboxes()` N_CALLS 회 호출
 4) crop 픽셀 좌표를 원본 픽셀 좌표로 역변환 후, 원본 short_side 로 정규화
    → baseline(전체 이미지 short_side 기준) 과 동일 축에서 비교

판정 (baseline 과 동일 임계):
 - stable       ≤ 0.010
 - borderline   ≤ 0.025
 - unstable     > 0.025
 기준: VERDICT_PARTS = (left_eye, right_eye, nose) 중 max(cx_std_ratio, cy_std_ratio) 최댓값.

추천 (recommendation):
 - stable / borderline → "proceed_with_2stage_crop"
 - unstable            → "escalate_to_fine_tune"

stdout 에 JSON 한 줄 출력. raw 로그는 stderr.

실행:
  cd backend
  source venv/bin/activate
  python tests/run_2stage_crop_probe.py

전제:
 - 환경변수 GOOGLE_API_KEY 필요
 - 테스트 이미지 고정: /Users/soyeon/Downloads/IMG_7641.jpg
 - Gemini 재시도 루프 금지 — 호출 실패는 그대로 기록
 - 총 Gemini 호출: head_bbox 1회 + face parts 5회 = 6회
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

import pillow_heif

pillow_heif.register_heif_opener()

from dotenv import load_dotenv
from PIL import Image

# backend/ 루트를 sys.path 에 추가 (services.* import 를 위해)
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

load_dotenv(_BACKEND_ROOT / ".env")

from google import genai  # noqa: E402

from services.gemini_pipeline import _detect_face_parts_bboxes  # noqa: E402
from services.inpaint_pipeline import _detect_full_head_bbox  # noqa: E402

# ── 설정 ──────────────────────────────────────────────────────────────────────

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")
PART_NAMES = ("left_eye", "right_eye", "nose", "mouth")
VERDICT_PARTS = ("left_eye", "right_eye", "nose")

N_CALLS = 5                    # crop 에서 face parts 호출 횟수
CROP_PADDING_RATIO = 0.10      # head_bbox 둘레에 붙일 padding 비율 (max(hw, hh) 기준)

STABLE_THRESHOLD = 0.010
BORDERLINE_THRESHOLD = 0.025

# baseline (run_geometry_variance.py 실측값, 전체 이미지 단일 호출 기준)
BASELINE_FULL_IMAGE = {
    "cx_std_ratio_max": 0.021,
    "cy_std_ratio_max": 0.068,
    "max_std_ratio": 0.068,
    "basis_part": "nose",
    "verdict": "unstable",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("run_2stage_crop_probe")


# ── 유틸 ──────────────────────────────────────────────────────────────────────


def _std(values: list[float]) -> float:
    """표본 표준편차. n<2 면 0.0 반환."""
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def _verdict(max_ratio: float) -> str:
    if max_ratio <= STABLE_THRESHOLD:
        return "stable"
    if max_ratio <= BORDERLINE_THRESHOLD:
        return "borderline"
    return "unstable"


def _pad_and_crop(
    image_bytes: bytes,
    head_bbox: dict,
    padding_ratio: float,
) -> tuple[bytes, tuple[int, int, int, int], tuple[int, int]]:
    """head_bbox 에 padding 을 주어 원본 이미지를 crop 한다.

    Returns:
        crop_bytes: crop 된 PNG bytes
        crop_box  : (x0, y0, x1, y1) — 원본 픽셀 좌표계의 crop 영역
        crop_size : (cw, ch) — crop 이미지의 (너비, 높이)
    """
    img = Image.open(io.BytesIO(image_bytes))
    img_w, img_h = img.size

    hw = head_bbox["xmax"] - head_bbox["xmin"]
    hh = head_bbox["ymax"] - head_bbox["ymin"]
    pad = int(max(hw, hh) * padding_ratio)

    x0 = max(0, head_bbox["xmin"] - pad)
    y0 = max(0, head_bbox["ymin"] - pad)
    x1 = min(img_w, head_bbox["xmax"] + pad)
    y1 = min(img_h, head_bbox["ymax"] + pad)

    crop = img.crop((x0, y0, x1, y1))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue(), (x0, y0, x1, y1), crop.size


async def _one_call_parts_in_full_coords(
    crop_bytes: bytes,
    client,
    crop_origin: tuple[int, int],
) -> dict[str, tuple[float, float, float, float]]:
    """crop 이미지에 대해 face parts 를 1회 호출하고, 좌표를 원본 픽셀계로 역변환.

    crop_origin = (x0, y0) : 원본 이미지 기준 crop 의 좌상단 좌표.
    `_detect_face_parts_bboxes` 는 내부에서 이미 crop 이미지 픽셀로 변환된 bbox 를 돌려주므로,
    여기서는 x0/y0 만 더해 원본 픽셀계로 옮긴다.
    """
    x0, y0 = crop_origin
    try:
        parts = await _detect_face_parts_bboxes(crop_bytes, client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_detect_face_parts_bboxes 예외 — 빈 결과: %s", exc)
        return {}

    out: dict[str, tuple[float, float, float, float]] = {}
    for p in parts:
        name = p.get("name")
        if name not in PART_NAMES or name in out:
            continue
        try:
            xmin = float(p["xmin"]) + x0
            ymin = float(p["ymin"]) + y0
            xmax = float(p["xmax"]) + x0
            ymax = float(p["ymax"]) + y0
        except (KeyError, TypeError, ValueError):
            continue
        out[name] = (xmin, ymin, xmax, ymax)
    return out


def _evaluate(
    calls: list[dict[str, tuple[float, float, float, float]]],
    short_side: int,
) -> dict:
    """N 회 호출 결과에서 파트별 cx/cy std_ratio 와 verdict 산출.

    short_side: 원본 이미지 short_side 픽셀 — baseline 과 동일 축.
    """
    parts_metrics: dict[str, dict] = {}

    for name in VERDICT_PARTS + ("mouth",):
        cxs: list[float] = []
        cys: list[float] = []
        for got in calls:
            bbox = got.get(name)
            if bbox is None:
                continue
            xmin, ymin, xmax, ymax = bbox
            cxs.append((xmin + xmax) / 2.0)
            cys.append((ymin + ymax) / 2.0)
        n_hits = len(cxs)
        cx_std = _std(cxs)
        cy_std = _std(cys)
        parts_metrics[name] = {
            "n_hits": n_hits,
            "cx_std_ratio": (cx_std / short_side) if short_side > 0 else 0.0,
            "cy_std_ratio": (cy_std / short_side) if short_side > 0 else 0.0,
        }

    candidates: list[tuple[float, str]] = []
    for name in VERDICT_PARTS:
        m = parts_metrics.get(name, {})
        if not m or m.get("n_hits", 0) < 2:
            continue
        worst = max(m["cx_std_ratio"], m["cy_std_ratio"])
        candidates.append((worst, name))

    if not candidates:
        return {
            "parts": parts_metrics,
            "max_std_ratio": float("inf"),
            "basis_part": "no_verdict_parts_detected",
            "verdict": "unstable",
        }

    max_ratio, basis_part = max(candidates, key=lambda x: x[0])
    return {
        "parts": {
            name: {
                "n_hits": parts_metrics[name]["n_hits"],
                "cx_std_ratio": parts_metrics[name]["cx_std_ratio"],
                "cy_std_ratio": parts_metrics[name]["cy_std_ratio"],
            }
            for name in (VERDICT_PARTS + ("mouth",))
        },
        "max_std_ratio": max_ratio,
        "basis_part": basis_part,
        "verdict": _verdict(max_ratio),
    }


def _pick_recommendation(verdict: str) -> str:
    if verdict in ("stable", "borderline"):
        return "proceed_with_2stage_crop"
    return "escalate_to_fine_tune"


# ── 메인 ──────────────────────────────────────────────────────────────────────


async def _run() -> dict:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        print(
            json.dumps({"error": "GOOGLE_API_KEY 환경변수가 설정되지 않음"}),
            flush=True,
        )
        sys.exit(2)

    if not IMAGE_PATH.exists():
        print(
            json.dumps({"error": f"이미지 파일 없음: {IMAGE_PATH}"}),
            flush=True,
        )
        sys.exit(2)

    image_bytes = IMAGE_PATH.read_bytes()
    with Image.open(io.BytesIO(image_bytes)) as img:
        img_w, img_h = img.size
    short_side = min(img_w, img_h)
    logger.info(
        "이미지: %s size=(%d, %d) short_side=%d",
        IMAGE_PATH.name, img_w, img_h, short_side,
    )

    client = genai.Client(api_key=api_key)

    # Step 1: head_bbox 1회 탐지 — crop 좌표 고정
    logger.info("Step 1: head_bbox 탐지 호출...")
    head_bbox = await _detect_full_head_bbox(image_bytes, client)
    if head_bbox is None:
        return {
            "error": "head_bbox 탐지 실패",
            "baseline_full_image": BASELINE_FULL_IMAGE,
        }
    logger.info("head_bbox (원본 픽셀): %s", head_bbox)

    # Step 2: crop
    crop_bytes, crop_box, crop_size = _pad_and_crop(
        image_bytes, head_bbox, CROP_PADDING_RATIO
    )
    x0, y0, x1, y1 = crop_box
    cw, ch = crop_size
    logger.info(
        "crop: box=(%d,%d,%d,%d) size=(%d,%d) padding_ratio=%.2f",
        x0, y0, x1, y1, cw, ch, CROP_PADDING_RATIO,
    )

    # Step 3: crop 에서 face parts N 회 호출
    calls: list[dict[str, tuple[float, float, float, float]]] = []
    for idx in range(1, N_CALLS + 1):
        logger.info("Step 3: face parts 호출 %d/%d ...", idx, N_CALLS)
        got = await _one_call_parts_in_full_coords(
            crop_bytes, client, crop_origin=(x0, y0)
        )
        logger.info(
            "call %d 결과 (원본 픽셀): %s",
            idx,
            json.dumps(
                {k: list(v) for k, v in got.items()}, ensure_ascii=False
            ),
        )
        calls.append(got)

    # Step 4: 평가 — 원본 short_side 기준 정규화
    ev = _evaluate(calls, short_side=short_side)
    recommendation = _pick_recommendation(ev["verdict"])

    return {
        "image_size": [img_w, img_h],
        "short_side": short_side,
        "head_bbox": head_bbox,
        "crop_box": [x0, y0, x1, y1],
        "crop_size": [cw, ch],
        "crop_padding_ratio": CROP_PADDING_RATIO,
        "n_calls": N_CALLS,
        "baseline_full_image": BASELINE_FULL_IMAGE,
        "stage2_crop": ev,
        "recommendation": recommendation,
    }


def main() -> None:
    result = asyncio.run(_run())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
