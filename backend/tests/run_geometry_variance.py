"""
Geometry variance 측정 — VLM 좌표 안정성 진단 (anchor-inpainting v1.0 검증 선행 절차).

`services.gemini_pipeline._detect_face_parts_bboxes` 를 동일 이미지에 대해 5회 독립 호출한 뒤,
파트별(cx, cy, w, h)의 평균·표준편차·범위를 산출해 JSON 으로 stdout 출력한다.

실행:
  cd backend
  source venv/bin/activate
  python tests/run_geometry_variance.py

전제:
  - 환경변수 GOOGLE_API_KEY 가 설정되어 있어야 한다.
  - 테스트 이미지는 고정 경로 /Users/soyeon/Downloads/IMG_7641.jpg
  - 재시도 루프 금지: 5회는 독립 실행. 파트 누락은 해당 파트의 n_hits 감소로만 반영.
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

# 재사용: gemini_pipeline 의 _detect_face_parts_bboxes
from google import genai  # noqa: E402

from services.gemini_pipeline import _detect_face_parts_bboxes  # noqa: E402

# ── 설정 ──────────────────────────────────────────────────────────────────────

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")
N_CALLS = 5
PART_NAMES = ("left_eye", "right_eye", "nose", "mouth")
# verdict 판정 대상 파트 (mouth 제외)
VERDICT_PARTS = ("left_eye", "right_eye", "nose")

STABLE_THRESHOLD = 0.01
BORDERLINE_THRESHOLD = 0.025

# raw 로그는 stderr 로
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("run_geometry_variance")


# ── 유틸 ──────────────────────────────────────────────────────────────────────


def _std(values: list[float]) -> float:
    """표본 표준편차. n<2 면 0.0 반환."""
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return statistics.fmean(values)


def _part_metrics(
    cxs: list[float],
    cys: list[float],
    ws: list[float],
    hs: list[float],
    short_side: int,
) -> dict:
    cx_std = _std(cxs)
    cy_std = _std(cys)
    return {
        "n_hits": len(cxs),
        "cx_mean": _mean(cxs),
        "cy_mean": _mean(cys),
        "cx_std": cx_std,
        "cy_std": cy_std,
        "w_std": _std(ws),
        "h_std": _std(hs),
        "cx_std_ratio": (cx_std / short_side) if short_side > 0 else 0.0,
        "cy_std_ratio": (cy_std / short_side) if short_side > 0 else 0.0,
        "cx_min": min(cxs) if cxs else 0.0,
        "cx_max": max(cxs) if cxs else 0.0,
        "cy_min": min(cys) if cys else 0.0,
        "cy_max": max(cys) if cys else 0.0,
    }


def _verdict(parts_metrics: dict) -> tuple[str, str]:
    """verdict (stable/borderline/unstable) 과 근거 파트명 반환.

    VERDICT_PARTS (left_eye, right_eye, nose) 중 max(cx_std_ratio, cy_std_ratio) 의
    최댓값을 기준으로 판정.
    """
    candidates: list[tuple[float, str]] = []
    for name in VERDICT_PARTS:
        m = parts_metrics.get(name, {})
        if not m or m.get("n_hits", 0) < 2:
            # 데이터 부족 — 해당 파트는 verdict 근거에서 제외
            continue
        worst = max(m["cx_std_ratio"], m["cy_std_ratio"])
        candidates.append((worst, name))

    if not candidates:
        return "unstable", "no_verdict_parts_detected"

    worst_ratio, worst_part = max(candidates, key=lambda x: x[0])
    if worst_ratio <= STABLE_THRESHOLD:
        v = "stable"
    elif worst_ratio <= BORDERLINE_THRESHOLD:
        v = "borderline"
    else:
        v = "unstable"
    return v, worst_part


# ── 메인 로직 ─────────────────────────────────────────────────────────────────


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
        "이미지: %s  size=(%d, %d)  short_side=%d",
        IMAGE_PATH.name,
        img_w,
        img_h,
        short_side,
    )

    client = genai.Client(api_key=api_key)

    # 파트별 관측치 누적
    observations: dict[str, dict[str, list[float]]] = {
        name: {"cx": [], "cy": [], "w": [], "h": []} for name in PART_NAMES
    }

    for call_idx in range(1, N_CALLS + 1):
        logger.info("[call %d/%d] _detect_face_parts_bboxes 호출 중...", call_idx, N_CALLS)
        try:
            parts = await _detect_face_parts_bboxes(image_bytes, client)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[call %d] 예외 발생 — 빈 결과로 기록: %s", call_idx, exc)
            parts = []

        # raw 로그 (stderr)
        try:
            logger.info(
                "[call %d] raw parts (n=%d): %s",
                call_idx,
                len(parts),
                json.dumps(parts, ensure_ascii=False),
            )
        except Exception:
            logger.info("[call %d] raw parts (직렬화 실패, n=%d)", call_idx, len(parts))

        seen_names = set()
        for p in parts:
            name = p.get("name")
            if name not in PART_NAMES or name in seen_names:
                continue
            try:
                xmin = float(p["xmin"])
                ymin = float(p["ymin"])
                xmax = float(p["xmax"])
                ymax = float(p["ymax"])
            except (KeyError, TypeError, ValueError):
                continue
            cx = (xmin + xmax) / 2.0
            cy = (ymin + ymax) / 2.0
            w = xmax - xmin
            h = ymax - ymin
            observations[name]["cx"].append(cx)
            observations[name]["cy"].append(cy)
            observations[name]["w"].append(w)
            observations[name]["h"].append(h)
            seen_names.add(name)

        missing = [n for n in PART_NAMES if n not in seen_names]
        if missing:
            logger.warning("[call %d] 누락 파트: %s", call_idx, missing)

    # 파트별 지표 산출
    parts_metrics: dict[str, dict] = {}
    for name in PART_NAMES:
        o = observations[name]
        parts_metrics[name] = _part_metrics(o["cx"], o["cy"], o["w"], o["h"], short_side)

    verdict, basis = _verdict(parts_metrics)

    result = {
        "image_size": [img_w, img_h],
        "n_calls": N_CALLS,
        "parts": parts_metrics,
        "verdict": verdict,
        "verdict_basis_part": basis,
    }
    return result


def main() -> None:
    result = asyncio.run(_run())
    # stdout 에 JSON 한 줄
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
