"""
Geometry median probe — "N회 호출 median으로 합치면 분산이 허용 임계 이내로 떨어지는가" 검증.

`services.gemini_pipeline._detect_face_parts_bboxes` 를 동일 이미지에 대해
 - 조건 N=3: 3회 호출 median bbox 1장 → 3회 반복 → median bbox 3장
 - 조건 N=5: 5회 호출 median bbox 1장 → 3회 반복 → median bbox 3장
총 Gemini 호출 = 3*3 + 5*3 = 24회.

각 조건에서 median bbox 3장에 대해 파트별 cx/cy 표준편차 비율을 계산해
 - ≤ 0.010 stable / ≤ 0.025 borderline / > 0.025 unstable 로 판정.
추천(recommendation):
 - N=3 또는 N=5 중 하나라도 borderline 이상(≤ 0.025) → "proceed_with_median_N=<n>"
 - 둘 다 unstable → "escalate_to_cv_deterministic"

stdout 에 JSON 한 줄 출력. raw 로그는 stderr.

실행:
  cd backend
  source venv/bin/activate
  python tests/run_geometry_median_probe.py

전제:
  - 환경변수 GOOGLE_API_KEY 필요
  - 테스트 이미지 고정: /Users/soyeon/Downloads/IMG_7641.jpg
  - Gemini 재시도 루프 금지 — 호출 실패는 그대로 기록
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

# ── 설정 ──────────────────────────────────────────────────────────────────────

IMAGE_PATH = Path("/Users/soyeon/Downloads/IMG_7641.jpg")
PART_NAMES = ("left_eye", "right_eye", "nose", "mouth")
VERDICT_PARTS = ("left_eye", "right_eye", "nose")

CONDITIONS = (3, 5)        # N (median 묶음 크기)
REPEATS_PER_CONDITION = 3  # 조건당 median bbox 반복 횟수
MIN_HITS_FOR_MEDIAN = 3    # median 반영 최소 hit

STABLE_THRESHOLD = 0.010
BORDERLINE_THRESHOLD = 0.025

# 앞선 variance 실측 baseline (run_geometry_variance.py 결과 기반 하드코딩, 비교용)
BASELINE_SINGLE_CALL = {
    "cx_std_ratio_max": 0.021,
    "cy_std_ratio_max": 0.068,
    "basis_part": "nose",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("run_geometry_median_probe")


# ── 유틸 ──────────────────────────────────────────────────────────────────────


def _std(values: list[float]) -> float:
    """표본 표준편차. n<2 면 0.0 반환."""
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


async def _one_call_parts(
    image_bytes: bytes, client
) -> dict[str, tuple[float, float, float, float]]:
    """한 번의 _detect_face_parts_bboxes 호출 결과를 파트명 → (xmin, ymin, xmax, ymax) 로 변환.

    중복 파트는 첫 번째만 사용. 좌표 파싱 실패 파트는 누락 처리.
    예외 발생 시 빈 dict 반환 (재시도 루프 없음 — 호출 단위로 실패 기록).
    """
    try:
        parts = await _detect_face_parts_bboxes(image_bytes, client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_detect_face_parts_bboxes 예외 — 빈 결과: %s", exc)
        return {}

    out: dict[str, tuple[float, float, float, float]] = {}
    for p in parts:
        name = p.get("name")
        if name not in PART_NAMES or name in out:
            continue
        try:
            xmin = float(p["xmin"])
            ymin = float(p["ymin"])
            xmax = float(p["xmax"])
            ymax = float(p["ymax"])
        except (KeyError, TypeError, ValueError):
            continue
        out[name] = (xmin, ymin, xmax, ymax)
    return out


async def _one_median_bbox(
    image_bytes: bytes, client, n: int, tag: str
) -> dict[str, tuple[float, float, float, float] | None]:
    """n 회 호출 후 파트별 field-wise median bbox 1장 생성.

    파트별 hit < MIN_HITS_FOR_MEDIAN 이면 해당 파트는 None (누락).
    """
    # 파트별 관측치 누적
    obs: dict[str, dict[str, list[float]]] = {
        name: {"xmin": [], "ymin": [], "xmax": [], "ymax": []} for name in PART_NAMES
    }

    for call_idx in range(1, n + 1):
        logger.info("[%s] call %d/%d 호출...", tag, call_idx, n)
        got = await _one_call_parts(image_bytes, client)
        logger.info(
            "[%s] call %d 결과: %s",
            tag,
            call_idx,
            json.dumps(
                {k: list(v) for k, v in got.items()}, ensure_ascii=False
            ),
        )
        for name, (xmin, ymin, xmax, ymax) in got.items():
            obs[name]["xmin"].append(xmin)
            obs[name]["ymin"].append(ymin)
            obs[name]["xmax"].append(xmax)
            obs[name]["ymax"].append(ymax)

    median_bbox: dict[str, tuple[float, float, float, float] | None] = {}
    for name in PART_NAMES:
        xs = obs[name]["xmin"]
        if len(xs) < MIN_HITS_FOR_MEDIAN:
            logger.warning(
                "[%s] 파트 %s hit=%d < %d → median 누락",
                tag, name, len(xs), MIN_HITS_FOR_MEDIAN,
            )
            median_bbox[name] = None
            continue
        xmin_m = statistics.median(obs[name]["xmin"])
        ymin_m = statistics.median(obs[name]["ymin"])
        xmax_m = statistics.median(obs[name]["xmax"])
        ymax_m = statistics.median(obs[name]["ymax"])
        median_bbox[name] = (xmin_m, ymin_m, xmax_m, ymax_m)

    logger.info(
        "[%s] median bbox: %s",
        tag,
        json.dumps(
            {k: (list(v) if v else None) for k, v in median_bbox.items()},
            ensure_ascii=False,
        ),
    )
    return median_bbox


def _evaluate_condition(
    median_bboxes: list[dict[str, tuple[float, float, float, float] | None]],
    short_side: int,
    n: int,
) -> dict:
    """조건별 median bbox 리스트(길이 = REPEATS_PER_CONDITION)에 대한 지표 산출.

    파트별로 median bbox 중 None 이 아닌 것만 모아 cx/cy std_ratio 계산.
    n_hits < 2 면 해당 파트는 std 계산 제외.
    """
    parts_metrics: dict[str, dict] = {}

    for name in VERDICT_PARTS + ("mouth",):  # mouth 는 기록만, verdict 에는 미반영
        cxs: list[float] = []
        cys: list[float] = []
        for mb in median_bboxes:
            bbox = mb.get(name)
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

    # verdict: VERDICT_PARTS 중 max(cx_std_ratio, cy_std_ratio) 최댓값 기준
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
        if max_ratio <= STABLE_THRESHOLD:
            verdict = "stable"
        elif max_ratio <= BORDERLINE_THRESHOLD:
            verdict = "borderline"
        else:
            verdict = "unstable"

    return {
        "total_calls": n * REPEATS_PER_CONDITION,
        "medians_collected": len(median_bboxes),
        "parts": {
            name: {
                "cx_std_ratio": parts_metrics[name]["cx_std_ratio"],
                "cy_std_ratio": parts_metrics[name]["cy_std_ratio"],
            }
            for name in VERDICT_PARTS
        },
        "max_std_ratio": max_ratio,
        "basis_part": basis_part,
        "verdict": verdict,
    }


def _pick_recommendation(
    results_by_n: dict[int, dict],
) -> str:
    """N=3, N=5 중 하나라도 borderline 이상(≤ 0.025)이면 proceed_with_median_N=<n>.
    N=3 우선. 둘 다 unstable 이면 escalate.
    """
    for n in CONDITIONS:
        v = results_by_n[n]["verdict"]
        if v in ("stable", "borderline"):
            return f"proceed_with_median_N={n}"
    return "escalate_to_cv_deterministic"


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

    conditions_out: dict[str, dict] = {}
    results_by_n: dict[int, dict] = {}
    for n in CONDITIONS:
        median_bboxes: list[dict[str, tuple[float, float, float, float] | None]] = []
        for rep in range(1, REPEATS_PER_CONDITION + 1):
            tag = f"N={n} rep={rep}/{REPEATS_PER_CONDITION}"
            mb = await _one_median_bbox(image_bytes, client, n, tag)
            median_bboxes.append(mb)

        ev = _evaluate_condition(median_bboxes, short_side, n)
        results_by_n[n] = ev
        conditions_out[f"N={n}"] = ev

    recommendation = _pick_recommendation(results_by_n)

    return {
        "image_size": [img_w, img_h],
        "baseline_single_call": BASELINE_SINGLE_CALL,
        "conditions": conditions_out,
        "recommendation": recommendation,
    }


def main() -> None:
    result = asyncio.run(_run())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
