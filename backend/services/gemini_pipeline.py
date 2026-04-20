"""
Google Gemini 기반 이미지 생성 파이프라인.
모든 Gemini API 호출은 반드시 이 모듈을 통해서만 이루어진다.

변환 전략:
  - Gemini: 전체 이미지 스타일 변환
  - 프롬프트 강화로 털 색상·특징 보존 지시
"""

import pillow_heif
pillow_heif.register_heif_opener()

import asyncio
import io
import json
import logging
import os
import re
from typing import Optional

import cloudinary
import cloudinary.uploader
import httpx
from google import genai
from google.genai import types
from google.genai.types import GenerateContentConfig, Part
from PIL import Image, ImageFilter

from services.image_utils import _convert_to_jpeg_if_needed, _detect_mime_type
from services.style_prompts import get_prompt

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

_MODEL_GEMINI = "gemini-3.1-flash-image-preview"
_MODEL_ANALYSIS = "gemini-2.5-flash"

# 얼굴 파트 마스크 fallback 기준: blur >= 16인 활성 픽셀 개수 (alpha 총합 아님)
_MIN_MASK_PIXELS = 50

# v5 gating — 실패할 샘플은 합성하지 않는다(backend/CLAUDE.md 참조)
FACE_PRESERVE_MOUTH = False          # 원형 얼굴견 입안 검정/혀 분홍 → 마스크 번짐, 기본 OFF
_MAX_MASK_AREA_RATIO = 0.65          # 마스크가 bbox 65% 이상이면 털까지 번진 신호 → 스킵
_MAX_DRIFT_RATIO = 0.6               # 원본↔결과 bbox 중심 거리 / min(원본 short side). dst_parts 탐지로 paste 위치가 보정되므로 보수적 0.25는 과엄격이었음
_MIN_MANDATORY_PARTS_OK = 2          # 눈·코 중 2개 이상 gating이면 Gemini 재호출

# 견종명 → "this dog" 치환 패턴 (Gemini의 암묵적 색상 연상 방지)
_BREED_NAMES_PATTERN = re.compile(
    r"\b(maltese|poodle|bichon frise|bichon|maltipoo|pomeranian|"
    r"yorkshire terrier|yorkshire|shih tzu|papillon|"
    r"japanese spitz|spitz|bedlington terrier|bedlington|miniature bichon)\b",
    flags=re.IGNORECASE,
)


def _extract_dominant_fur_colors(image_bytes: bytes) -> str:
    """이미지 bytes에서 주요 털 색상을 추출해 프롬프트용 문자열로 반환한다.

    배경으로 추정되는 매우 밝은 색(RGB 각각 230 이상)과 매우 어두운 색(RGB 각각 30 이하)은
    제외하고, 남은 색상 중 상위 빈도 3~5가지의 RGB 값을 반환한다.

    Args:
        image_bytes: 원본 이미지 raw bytes

    Returns:
        프롬프트에 바로 삽입 가능한 색상 문자열.
        예: "rgb(180, 150, 120) and rgb(210, 180, 150)"
        색상 추출에 실패하면 빈 문자열을 반환한다.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = img.resize((100, 100), Image.LANCZOS)

        # getcolors는 (count, pixel) 튜플 리스트 반환
        colors = img.getcolors(maxcolors=100 * 100)
        if not colors:
            return ""

        # 배경 가능성이 높은 극단 색상 제거
        def _is_background(rgb: tuple) -> bool:
            r, g, b = rgb
            # 순수 흰색/검정 배경
            if (r >= 230 and g >= 230 and b >= 230) or (r <= 30 and g <= 30 and b <= 30):
                return True
            # 밝고 채도 낮은 무채색 배경 (아이보리/베이지/회색)
            brightness = (r + g + b) / 3.0
            cmax = max(r, g, b) / 255.0
            cmin = min(r, g, b) / 255.0
            sat = (cmax - cmin) / cmax if cmax > 0 else 0.0
            return brightness > 200 and sat < 0.15

        filtered = [(count, pixel) for count, pixel in colors if not _is_background(pixel)]
        if not filtered:
            return ""

        # 빈도 내림차순 정렬 후 상위 5가지 선택
        filtered.sort(key=lambda x: x[0], reverse=True)
        top_colors = [pixel for _, pixel in filtered[:5]]

        color_strings = [f"rgb({r}, {g}, {b})" for r, g, b in top_colors]
        return " and ".join(color_strings)

    except Exception as exc:
        logger.warning("[gemini_pipeline] 털 색상 추출 실패: %s", exc)
        return ""



async def _analyze_dog_features(image_bytes: bytes, gemini_client=None) -> str:
    """
    gemini-2.5-flash (텍스트 모델)로 원본 강아지 이미지의 핵심 특징을 분석합니다.
    분석 결과는 이미지 변환 프롬프트에 주입되어 얼굴 보존 정확도를 높입니다.
    gemini_client가 None이면 내부에서 생성합니다.
    """
    try:
        if gemini_client is None:
            gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
        mime_type = _detect_mime_type(image_bytes)
        image_part = Part.from_bytes(data=image_bytes, mime_type=mime_type)
        analysis_prompt = (
            "Analyze this dog photo and describe the following concisely in English:\n"
            "1. Eyes: color, shape, position\n"
            "2. Nose: color, shape\n"
            "3. Mouth/tongue: expression, tongue visible or not\n"
            "4. Pose: sitting/standing, paw position\n"
            "5. Fur color: main colors and pattern\n"
            "6. Background: brief description\n"
            "Be specific and concise. This will be used to preserve these features during grooming style transformation."
        )
        text_part = Part.from_text(text=analysis_prompt)
        response = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model=_MODEL_ANALYSIS,
            contents=[image_part, text_part],
        )
        analysis = response.text or ""
        logger.info("[gemini_pipeline] dog feature analysis completed (%d chars)", len(analysis))
        return analysis
    except Exception as e:
        logger.warning("[gemini_pipeline] feature analysis failed, skipping: %s", e)
        return ""



_FACE_PART_NAMES = ("left_eye", "right_eye", "nose", "mouth")


async def _detect_face_parts_bboxes(image_bytes: bytes, gemini_client) -> list[dict]:
    """눈(좌/우)·코·입 각각의 tight bbox를 float 퍼센트 좌표로 탐지한다.

    float 좌표(소수점 1자리)로 받아 픽셀 변환 시 round()로 정밀도 확보.

    반환: [{"name": str, "xmin": px, "ymin": px, "xmax": px, "ymax": px}, ...] (이미지 픽셀 단위)
    실패 시 빈 리스트 반환.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img_w, img_h = img.size

        mime_type = _detect_mime_type(image_bytes)
        image_part = Part.from_bytes(data=image_bytes, mime_type=mime_type)
        detection_prompt = (
            "Look at this dog photo. Detect a VERY TIGHT bounding box around each facial feature.\n"
            "\n"
            "Features to detect:\n"
            "- left_eye: ONLY the eyeball/iris of the dog's left eye (right side of image). Box edge must touch the eye rim.\n"
            "- right_eye: ONLY the eyeball/iris of the dog's right eye (left side of image). Box edge must touch the eye rim.\n"
            "- nose: ONLY the dark nose leather (the black/dark bump). Do NOT include surrounding fur.\n"
            "- mouth: ONLY the mouth opening / lip line. Do NOT include surrounding muzzle fur.\n"
            "\n"
            "CRITICAL: Make each box as TIGHT as possible. The box should NOT include any fur around the feature.\n"
            "\n"
            'Return ONLY this JSON (no markdown, no explanation):\n'
            '{"left_eye": {"xmin": 0-100, "ymin": 0-100, "xmax": 0-100, "ymax": 0-100}, '
            '"right_eye": {"xmin": 0-100, "ymin": 0-100, "xmax": 0-100, "ymax": 0-100}, '
            '"nose": {"xmin": 0-100, "ymin": 0-100, "xmax": 0-100, "ymax": 0-100}, '
            '"mouth": {"xmin": 0-100, "ymin": 0-100, "xmax": 0-100, "ymax": 0-100}}\n'
            "All values are integers from 0 to 100 representing PERCENTAGE of image dimensions. "
            "0=top-left corner, 100=bottom-right corner.\n"
            "WARNING: Do NOT return pixel coordinates (e.g. 381, 1024 etc). "
            "Return ONLY percentages 0-100. "
            "Example: if right eye is near center-left at about 35-45% from left, 35-40% from top, "
            "return right_eye: {xmin:35, ymin:35, xmax:45, ymax:40}."
        )
        def _parse_parts(data: dict) -> list[dict]:
            """JSON 응답에서 bbox 파트를 파싱한다. 유효하지 않으면 빈 리스트."""
            parsed = []
            # 값이 100 초과하는 경우를 픽셀 좌표로 감지
            all_vals = [
                float(data[nm][k])
                for nm in _FACE_PART_NAMES if nm in data
                for k in ("xmin", "ymin", "xmax", "ymax")
                if isinstance(data[nm], dict) and k in data[nm]
            ]
            # 모든 값이 100 이하여야 퍼센트로 유효 (하나라도 초과하면 픽셀 좌표로 오해석한 것)
            if all_vals and max(all_vals) > 100:
                logger.warning(
                    "[gemini_pipeline] face parts: 100 초과 값 감지 (max=%.0f) — 픽셀 좌표 반환으로 판단, 재시도",
                    max(all_vals),
                )
                return []  # 재시도 신호

            for name in _FACE_PART_NAMES:
                if name not in data:
                    continue
                d = data[name]
                if not isinstance(d, dict) or not {"xmin", "ymin", "xmax", "ymax"}.issubset(d.keys()):
                    continue
                pct_xmin = max(0.0, min(100.0, float(d["xmin"])))
                pct_ymin = max(0.0, min(100.0, float(d["ymin"])))
                pct_xmax = max(0.0, min(100.0, float(d["xmax"])))
                pct_ymax = max(0.0, min(100.0, float(d["ymax"])))
                xmin = round(pct_xmin / 100 * img_w)
                ymin = round(pct_ymin / 100 * img_h)
                xmax = round(pct_xmax / 100 * img_w)
                ymax = round(pct_ymax / 100 * img_h)
                if xmax - xmin < 4 or ymax - ymin < 4:
                    logger.warning(
                        "[gemini_pipeline] %s bbox 너무 작음 (%d×%d px) — 스킵",
                        name, xmax - xmin, ymax - ymin,
                    )
                    continue
                parsed.append({"name": name, "xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax})
            return parsed

        text_part = Part.from_text(text=detection_prompt)

        # 최대 2회 시도 (Gemini가 픽셀 좌표로 반환하는 경우 재시도)
        for attempt in range(2):
            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                model=_MODEL_ANALYSIS,
                contents=[image_part, text_part],
            )
            raw = response.text or ""
            json_match = re.search(r"\{[\s\S]*\}", raw)
            if not json_match:
                logger.warning("[gemini_pipeline] face parts 탐지 attempt=%d: JSON 파싱 실패", attempt + 1)
                continue
            data = json.loads(json_match.group())
            logger.warning("[gemini_pipeline] face parts attempt=%d raw JSON: %s", attempt + 1, json.dumps(data))
            parts = _parse_parts(data)
            if parts:
                logger.warning("[gemini_pipeline] face parts 탐지 성공 (attempt=%d): %d개 — %s",
                    attempt + 1, len(parts),
                    [(p["name"], p["xmin"], p["ymin"], p["xmax"], p["ymax"]) for p in parts])
                return parts
            if attempt == 0:
                logger.warning("[gemini_pipeline] face parts attempt=1 실패 — 재시도")

        logger.warning("[gemini_pipeline] face parts 탐지 2회 모두 실패 — 합성 스킵")
        return []
    except Exception as exc:
        logger.warning("[gemini_pipeline] face parts 탐지 실패: %s", exc)
        return []


def _color_correct_result(
    original_bytes: bytes,
    result_bytes: bytes,
    features_bbox: dict | None = None,
) -> bytes:
    """결과 이미지의 털 색상 분포를 원본에 맞춘다 (채널별 히스토그램 매칭).

    전략:
      1. 원본의 채도(> 0.20) 높은 털 픽셀을 레퍼런스 집합으로 선정
      2. 결과 이미지의 같은 위치 픽셀과 채널별 CDF를 계산해 LUT 생성
      3. LUT를 전체 이미지에 적용

    히스토그램 매칭은 평균만 맞추는 곱셈 보정과 달리 밝기 분포 전체(하이라이트 포함)를
    복원하므로, 원본의 밝고 따뜻한 하이라이트도 재현됨.

    실패 시 result_bytes 그대로 반환.
    """
    try:
        import numpy as np

        original = Image.open(io.BytesIO(original_bytes)).convert("RGB")
        result = Image.open(io.BytesIO(result_bytes)).convert("RGB")

        orig_w, orig_h = original.size
        res_w, res_h = result.size

        orig_arr = np.array(original, dtype=np.uint8)
        res_arr = np.array(result, dtype=np.uint8)

        # 원본 이미지에서 채도 기반 털 마스크 생성
        orig_f = orig_arr.astype(float)
        r_n = orig_f[..., 0] / 255.0
        g_n = orig_f[..., 1] / 255.0
        b_n = orig_f[..., 2] / 255.0
        cmax = np.maximum(np.maximum(r_n, g_n), b_n)
        cmin = np.minimum(np.minimum(r_n, g_n), b_n)
        sat = np.zeros_like(cmax)
        nz_mask = cmax > 0
        sat[nz_mask] = (cmax[nz_mask] - cmin[nz_mask]) / cmax[nz_mask]

        # sat > 0.10: 크림/베이지/골든처럼 채도가 낮은 털도 포함
        # (0.20이면 이 범위 털이 제외되어 회색으로 변한 부분이 보정 안 됨)
        fur_mask = sat > 0.10
        if features_bbox:
            fur_mask[features_bbox["ymin"]:features_bbox["ymax"],
                     features_bbox["xmin"]:features_bbox["xmax"]] = False

        if not fur_mask.any():
            logger.warning("[gemini_pipeline] 색상 보정: 유채색 털 없음 — 흰/회색 견종 스킵")
            return result_bytes

        # 결과 이미지 대응 마스크
        if (res_h, res_w) != (orig_h, orig_w):
            fm_img = Image.fromarray((fur_mask * 255).astype(np.uint8))
            fm_img = fm_img.resize((res_w, res_h), Image.NEAREST)
            res_fur_mask = np.array(fm_img) > 128
        else:
            res_fur_mask = fur_mask

        # 사전 확인: 평균 delta
        orig_mean = orig_arr[fur_mask].astype(float).mean(axis=0)
        res_mean = res_arr[res_fur_mask].astype(float).mean(axis=0)
        delta = float(np.abs(orig_mean - res_mean).mean())
        logger.warning(
            "[gemini_pipeline] 히스토그램 매칭 검사 — orig=%s res=%s delta=%.1f fur_px=%d",
            orig_mean.astype(int).tolist(),
            res_mean.astype(int).tolist(),
            delta,
            int(fur_mask.sum()),
        )

        if delta < 3:
            logger.warning("[gemini_pipeline] 색상 차이 미미 (%.1f < 3) — 보정 스킵", delta)
            return result_bytes

        # 채널별 히스토그램 매칭: 결과 털 분포 → 원본 털 분포로 매핑
        # apply_mask로 원본 fur_mask(res_fur_mask) 사용:
        #   - 결과 이미지의 채도로 판단하면 Gemini가 털을 회색으로 바꿨을 때 회색 털도 채도가 낮아
        #     보정 대상에서 제외되는 문제가 생김
        #   - 원본 위치 기반 마스크를 쓰면 Gemini가 색을 무엇으로 바꾸든 "원본에서 털이었던 위치"에만
        #     보정 적용 → 배경은 건드리지 않음
        corrected = res_arr.copy()
        for c in range(3):
            src_vals = res_arr[res_fur_mask, c]
            ref_vals = orig_arr[fur_mask, c]

            src_hist, _ = np.histogram(src_vals, bins=256, range=(0, 256))
            ref_hist, _ = np.histogram(ref_vals, bins=256, range=(0, 256))
            src_cdf = np.cumsum(src_hist).astype(float)
            ref_cdf = np.cumsum(ref_hist).astype(float)
            src_cdf /= src_cdf[-1]
            ref_cdf /= ref_cdf[-1]

            # src값 → ref값 매핑 LUT
            lut = np.zeros(256, dtype=np.uint8)
            ref_idx = 0
            for src_i in range(256):
                while ref_idx < 255 and ref_cdf[ref_idx] < src_cdf[src_i]:
                    ref_idx += 1
                lut[src_i] = ref_idx

            # 원본 털 위치(res_fur_mask=True)에만 LUT 적용 — 배경은 원본 결과 값 유지
            corrected_channel = lut[res_arr[..., c]]
            corrected[:, :, c] = np.where(res_fur_mask, corrected_channel, res_arr[:, :, c])

        corrected_img = Image.fromarray(corrected, "RGB")
        if corrected_img.size != (orig_w, orig_h):
            corrected_img = corrected_img.resize((orig_w, orig_h), Image.LANCZOS)

        buf = io.BytesIO()
        corrected_img.save(buf, format="JPEG", quality=95)
        logger.info("[gemini_pipeline] 히스토그램 매칭 완료 delta=%.1f", delta)
        return buf.getvalue()

    except Exception as exc:
        logger.warning("[gemini_pipeline] 색상 보정 실패, 원본 반환: %s", exc)
        return result_bytes


def _create_contour_mask(
    crop_img: "Image.Image",
    crop_w: int,
    crop_h: int,
    part_name: str = "",
) -> tuple["Image.Image", dict]:
    """크롭된 특징 이미지에서 어두운 픽셀(눈동자·코) 윤곽을 따라 블렌딩 마스크를 생성한다.

    전략:
      1. 3중 threshold 전략 (A: 상대 기준, B: 대비 기준, C: 최후 fallback)
      2. connectedComponentsWithStats로 component 선택 (눈/코: 혼합 점수, 입: 면적+거리)
      3. morphology open(보수적) → close(구멍 메우기)
      4. PIL MaxFilter 팽창 + GaussianBlur 페더링

    일반 타원/사각형 마스크보다 실제 눈·코 형태에 밀착됨.
    어두운 개에서 threshold가 낮아 active_pixels < 50 → ellipse fallback 문제 개선.

    Returns:
        (mask_img, meta): meta = {ellipse_fallback, active_pixels, mask_area_ratio, component_count}
        meta는 gating 판정용. zeros 반환 경로는 ellipse_fallback=True.
    """
    import cv2
    import numpy as np

    gray = np.array(crop_img.convert("L"), dtype=np.float32)
    min_side = min(crop_w, crop_h)

    # morphology 강도: open은 보수적(1회), close는 crop 크기 따라 1-2회
    open_iter  = 1
    close_iter = 1 if min_side < 40 else 2
    morph_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    # 3중 threshold 전략
    mean_g = float(np.mean(gray))
    std_g  = float(np.std(gray))
    thresholds = [
        min(80.0, float(np.percentile(gray, 25))),   # A: 기존
        mean_g - 0.8 * std_g,                         # B: 상대 대비
        float(np.percentile(gray, 20)),               # C: 최후 fallback
    ]

    cx_img = crop_w / 2.0
    cy_img = crop_h / 2.0
    chosen_mask = None
    chosen_component_count = 0

    for thr in thresholds:
        thr = max(thr, 0.0)
        raw = (gray <= thr).astype(np.uint8) * 255

        # morphology: open(noise 제거) → close(내부 구멍 메우기)
        cleaned = cv2.morphologyEx(raw,     cv2.MORPH_OPEN,  morph_kernel, iterations=open_iter)
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, morph_kernel, iterations=close_iter)

        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(cleaned)
        if num_labels <= 1:
            continue

        # label 0 = background 제외
        areas   = stats[1:, cv2.CC_STAT_AREA].astype(float)
        cent_xy = centroids[1:]
        dists   = np.hypot(cent_xy[:, 0] - cx_img, cent_xy[:, 1] - cy_img)

        if part_name == "mouth":
            # 입: 면적 상위 2개 후보 → 중심으로부터 거리 제약 (0.6 * min_side)
            dist_limit = 0.6 * min_side
            candidate_idx = np.argsort(areas)[::-1][:2]
            valid_idx = [i for i in candidate_idx if dists[i] < dist_limit]
            if not valid_idx:
                valid_idx = [int(np.argmin(dists))]
            top_labels = np.array(valid_idx) + 1   # +1: background offset
            strategy = f"mouth top2+dist_limit={dist_limit:.1f}"

        else:
            # 눈·코: 혼합 점수 = dist - alpha*sqrt(area) 최소값 선택
            # 큰 blob이 중앙에서 조금 멀어도 작은 잡음 blob보다 선택됨
            alpha = 0.3
            scores = dists - alpha * np.sqrt(areas)
            best_i = int(np.argmin(scores))
            top_labels = np.array([best_i + 1])
            strategy = f"eye/nose mixed_score(alpha={alpha})"
            logger.info(
                "[gemini_pipeline] contour_mask %s: scores=%s areas=%s dists=%s → best_i=%d",
                part_name,
                np.round(scores, 1).tolist(),
                np.round(areas, 0).tolist(),
                np.round(dists, 1).tolist(),
                best_i,
            )

        # 로그: 선택된 component 정보
        for i in (top_labels - 1):  # 0-indexed
            logger.info(
                "[gemini_pipeline] contour_mask %s: strategy=%s "
                "selected label=%d area=%.0f centroid=(%.1f,%.1f) dist=%.1f",
                part_name, strategy, i + 1, areas[i], cent_xy[i][0], cent_xy[i][1], dists[i],
            )

        selected = np.zeros_like(cleaned)
        for lbl in top_labels:
            selected[labels == lbl] = 255

        if int((selected >= 16).sum()) >= _MIN_MASK_PIXELS:
            chosen_mask = selected
            chosen_component_count = int(num_labels - 1)
            break

    if chosen_mask is None:
        logger.warning(
            "[gemini_pipeline] contour_mask %s: 모든 threshold 전략 실패 → ellipse fallback",
            part_name,
        )
        zeros_img = Image.fromarray(np.zeros((crop_h, crop_w), dtype=np.uint8), "L")
        meta = {
            "ellipse_fallback": True,
            "active_pixels": 0,
            "mask_area_ratio": 0.0,
            "component_count": 0,
        }
        return zeros_img, meta

    mask_img = Image.fromarray(chosen_mask, "L")

    # 팽창 + feathering
    # dilation 하한: min_side < 30이면 3, 그 외 5 — 작은 눈 crop에서 과팽창 방지
    dilation_lower = 3 if min_side < 30 else 5
    dilation_size = max(dilation_lower, int(min_side * 0.10))
    if dilation_size % 2 == 0:
        dilation_size += 1
    mask_img = mask_img.filter(ImageFilter.MaxFilter(dilation_size))

    # blur 하한: min_side < 30이면 2, 그 외 3 / 상한 5
    blur_lower = 2 if min_side < 30 else 3
    blur_r = min(5, max(blur_lower, int(min_side * 0.08)))
    mask_img = mask_img.filter(ImageFilter.GaussianBlur(radius=blur_r))

    logger.info(
        "[gemini_pipeline] contour_mask %s: dilation=%d blur_r=%d min_side=%d",
        part_name, dilation_size, blur_r, min_side,
    )

    final_arr = np.array(mask_img, dtype=np.uint8)
    active_pixels = int((final_arr >= 16).sum())
    bbox_area = float(crop_w * crop_h) if crop_w and crop_h else 0.0
    meta = {
        "ellipse_fallback": False,
        "active_pixels": active_pixels,
        "mask_area_ratio": (active_pixels / bbox_area) if bbox_area else 0.0,
        "component_count": chosen_component_count,
    }
    return mask_img, meta


def _seamless_clone_part(
    out: "Image.Image",
    part_crop_resized: "Image.Image",
    contour_mask: "Image.Image",
    dx1: int, dy1: int, dx2: int, dy2: int,
) -> "Image.Image | None":
    """cv2.seamlessClone으로 파트 크롭을 dst 이미지에 Poisson 합성한다.

    - src: part_crop_resized (파트 크롭, mask와 같은 크기)
    - dst: out (결과 이미지 전체)
    - mask: _create_contour_mask()로 만든 contour 마스크 → binary 변환
    - center: dst 기준 파트 중심 좌표

    경계 침범·크기 불일치·OpenCV 예외 발생 시 None 반환 → 호출부에서 paste fallback.
    """
    try:
        import cv2
        import numpy as np

        crop_w, crop_h = part_crop_resized.size
        dst_w, dst_h = out.size

        # seamlessClone은 center에서 src 절반 이상 dst 안에 있어야 함 (경계 침범 시 크래시)
        cx = dx1 + crop_w // 2
        cy = dy1 + crop_h // 2
        half_w = crop_w // 2 + 1
        half_h = crop_h // 2 + 1
        if cx - half_w < 0 or cy - half_h < 0 or cx + half_w >= dst_w or cy + half_h >= dst_h:
            logger.info("[gemini_pipeline] seamlessClone 경계 침범 — fallback")
            return None

        src_cv = cv2.cvtColor(np.array(part_crop_resized), cv2.COLOR_RGB2BGR)
        dst_cv = cv2.cvtColor(np.array(out), cv2.COLOR_RGB2BGR)

        # contour 마스크(soft) → binary: 32 이하 무시 (very faint edge 제거)
        mask_np = np.array(contour_mask)
        mask_cv = (mask_np > 32).astype(np.uint8) * 255

        if src_cv.shape[:2] != mask_cv.shape[:2]:
            logger.warning("[gemini_pipeline] seamlessClone src/mask 크기 불일치 — fallback")
            return None

        result_cv = cv2.seamlessClone(src_cv, dst_cv, mask_cv, (cx, cy), cv2.NORMAL_CLONE)
        return Image.fromarray(cv2.cvtColor(result_cv, cv2.COLOR_BGR2RGB))

    except Exception as exc:
        logger.warning("[gemini_pipeline] seamlessClone 실패: %s", exc)
        return None


def _compute_drift_ratio(
    part: dict,
    dst_lookup: dict,
    orig_w: int,
    orig_h: int,
    res_w: int,
    res_h: int,
) -> float | None:
    """원본 파트 bbox 중심을 결과 이미지 좌표로 비율 스케일 투영한 뒤
    결과 이미지 탐지 파트 중심과의 픽셀 거리를 min(원본 short side)로 정규화한다.

    비율 스케일만 사용 — affine/회전/perspective 금지(backend/CLAUDE.md Phase 14).
    dst_parts 탐지 결과가 없으면 None.
    """
    dst = dst_lookup.get(part["name"])
    if not dst:
        return None

    orig_cx = (part["xmin"] + part["xmax"]) / 2.0
    orig_cy = (part["ymin"] + part["ymax"]) / 2.0
    projected_cx = orig_cx / orig_w * res_w
    projected_cy = orig_cy / orig_h * res_h

    dst_cx = (dst["xmin"] + dst["xmax"]) / 2.0
    dst_cy = (dst["ymin"] + dst["ymax"]) / 2.0

    dx = projected_cx - dst_cx
    dy = projected_cy - dst_cy
    distance = (dx * dx + dy * dy) ** 0.5

    orig_short = float(min(part["xmax"] - part["xmin"], part["ymax"] - part["ymin"]))
    if orig_short <= 0:
        return None
    return distance / orig_short


def _composite_face_parts(
    original_bytes: bytes,
    result_bytes: bytes,
    face_parts: list[dict],
    dst_parts: list[dict] | None = None,
    padding_ratio: float = 0.06,
) -> tuple[bytes, list[dict]]:
    """Gemini 결과 위에 원본 얼굴 파트(눈·코·입) 픽셀을 개별 합성한다.

    각 파트마다 실제 어두운 픽셀 윤곽(_create_contour_mask)을 따라 마스크 생성 →
    눈·코·입 테두리를 자연스럽게 블렌딩하고 주변 털은 건드리지 않음.

    dst_parts: 결과 이미지에서 탐지한 face_parts — 제공 시 결과 이미지의 실제 파트
               위치를 목적지로 사용 (Gemini 얼굴 이동 보정). 없으면 비율 변환 fallback.

    Returns:
        (result_bytes, meta_list):
          meta_list[i] = {name, skip_reason, ellipse_fallback, active_pixels,
                          mask_area_ratio, drift_ratio, component_count}
          skip_reason: None(합성 성공) | "disabled" | "active_pixels_low" |
                       "ellipse_fallback" | "mask_area_too_large" | "drift_too_large" |
                       "crop_zero"
    실패 시 (result_bytes, []) 반환.
    """
    meta_list: list[dict] = []
    if not face_parts:
        return result_bytes, meta_list

    try:
        original = Image.open(io.BytesIO(original_bytes)).convert("RGB")
        result = Image.open(io.BytesIO(result_bytes)).convert("RGB")

        orig_w, orig_h = original.size
        res_w, res_h = result.size

        out = result.copy()

        # 결과 이미지 탐지 결과를 이름으로 조회 (없으면 빈 dict → fallback)
        dst_lookup = {p["name"]: p for p in (dst_parts or [])}

        for part in face_parts:
            # mouth 기본 OFF — 원형 얼굴견에서 입안 검정/혀 분홍으로 마스크 번짐
            if part["name"] == "mouth" and not FACE_PRESERVE_MOUTH:
                logger.info("[gemini_pipeline] mouth skipped (FACE_PRESERVE_MOUTH=False)")
                meta_list.append({"name": "mouth", "skip_reason": "disabled"})
                continue

            fw = part["xmax"] - part["xmin"]
            fh = part["ymax"] - part["ymin"]
            # fw/fh가 0이면 pad도 0 → crop_w=0 방지: 최소 10px padding 보장
            pad = max(10, int(max(fw, fh) * padding_ratio))

            ox1 = max(0, part["xmin"] - pad)
            oy1 = max(0, part["ymin"] - pad)
            ox2 = min(orig_w, part["xmax"] + pad)
            oy2 = min(orig_h, part["ymax"] + pad)

            # 목적지 좌표: 결과 이미지에서 탐지한 파트 위치 우선, 없으면 비율 변환
            dst = dst_lookup.get(part["name"])
            if dst:
                dst_pad = max(10, int(max(dst["xmax"] - dst["xmin"], dst["ymax"] - dst["ymin"]) * padding_ratio))
                rx1 = max(0, dst["xmin"] - dst_pad)
                ry1 = max(0, dst["ymin"] - dst_pad)
                rx2 = min(res_w, dst["xmax"] + dst_pad)
                ry2 = min(res_h, dst["ymax"] + dst_pad)
                logger.info("[gemini_pipeline] %s 목적지: 결과 이미지 탐지 좌표 사용 (%d,%d,%d,%d)",
                            part["name"], rx1, ry1, rx2, ry2)
            else:
                # fallback: 원본 좌표 비율 변환 (결과 이미지 탐지 실패 시)
                rx1 = int(ox1 / orig_w * res_w)
                ry1 = int(oy1 / orig_h * res_h)
                rx2 = int(ox2 / orig_w * res_w)
                ry2 = int(oy2 / orig_h * res_h)
                logger.info("[gemini_pipeline] %s 목적지: 비율 변환 fallback (%d,%d,%d,%d)",
                            part["name"], rx1, ry1, rx2, ry2)

            crop_w = rx2 - rx1
            crop_h = ry2 - ry1
            if crop_w <= 0 or crop_h <= 0:
                logger.warning("[gemini_pipeline] %s 크롭 크기 0 — 스킵", part["name"])
                meta_list.append({"name": part["name"], "skip_reason": "crop_zero"})
                continue

            part_crop = original.crop((ox1, oy1, ox2, oy2))
            part_crop_resized = part_crop.resize((crop_w, crop_h), Image.LANCZOS)

            # 실제 윤곽 기반 마스크 + meta (gating 판정용)
            part_mask, mask_meta = _create_contour_mask(
                part_crop_resized, crop_w, crop_h, part_name=part["name"]
            )

            # drift 계산: 원본↔결과 bbox 중심 비율 스케일 투영 후 거리/짧은변 정규화
            drift_ratio = _compute_drift_ratio(part, dst_lookup, orig_w, orig_h, res_w, res_h)
            mask_meta["drift_ratio"] = drift_ratio

            # Gating 판정 — 실패할 샘플은 합성하지 않는다
            skip_reason = None
            if mask_meta["active_pixels"] < _MIN_MASK_PIXELS:
                skip_reason = "active_pixels_low"
            elif mask_meta["ellipse_fallback"]:
                skip_reason = "ellipse_fallback"
            elif mask_meta["mask_area_ratio"] > _MAX_MASK_AREA_RATIO:
                skip_reason = "mask_area_too_large"
            elif drift_ratio is not None and drift_ratio > _MAX_DRIFT_RATIO:
                skip_reason = "drift_too_large"

            if skip_reason:
                logger.warning(
                    "[gemini_pipeline] %s gating skip: %s (active_px=%d area=%.2f drift=%s)",
                    part["name"], skip_reason,
                    mask_meta["active_pixels"], mask_meta["mask_area_ratio"],
                    f"{drift_ratio:.2f}" if drift_ratio is not None else "n/a",
                )
                meta_list.append({"name": part["name"], "skip_reason": skip_reason, **mask_meta})
                continue

            meta_list.append({"name": part["name"], "skip_reason": None, **mask_meta})

            # 눈: paste — seamlessClone은 눈 색 변화 일으킴 (Phase 26)
            # 코·입: seamlessClone → paste fallback
            if part["name"] in ("left_eye", "right_eye"):
                out.paste(part_crop_resized, (rx1, ry1), mask=part_mask)
                logger.info("[gemini_pipeline] %s 합성 (paste)", part["name"])
            else:
                cloned = _seamless_clone_part(out, part_crop_resized, part_mask, rx1, ry1, rx2, ry2)
                if cloned is not None:
                    out = cloned
                    logger.info("[gemini_pipeline] %s 합성 (seamless clone)", part["name"])
                else:
                    out.paste(part_crop_resized, (rx1, ry1), mask=part_mask)
                    logger.info("[gemini_pipeline] %s 합성 (paste fallback)", part["name"])

        if out.size != (orig_w, orig_h):
            out = out.resize((orig_w, orig_h), Image.LANCZOS)

        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=95)
        logger.info("[gemini_pipeline] 얼굴 파트 합성 완료 (%d개)", len(face_parts))
        return buf.getvalue(), meta_list

    except Exception as exc:
        logger.warning("[gemini_pipeline] 얼굴 파트 합성 실패, 원본 결과 반환: %s", exc)
        return result_bytes, meta_list


def _is_color_acceptable(original_bytes: bytes, result_bytes: bytes) -> bool:
    """결과 이미지의 색상이 원본과 충분히 유사한지 확인한다.

    이미지 중앙 50% 영역의 평균 채도를 비교.
    결과 채도가 원본의 50% 미만이면 색 소실(회색화) 판정 → False.
    원본 채도가 낮은 견종(흰/회색)은 체크 스킵 → True.
    """
    try:
        import numpy as np

        orig = Image.open(io.BytesIO(original_bytes)).convert("RGB")
        res = Image.open(io.BytesIO(result_bytes)).convert("RGB")

        orig_s = np.array(orig.resize((100, 100))).astype(float) / 255.0
        res_s = np.array(res.resize((100, 100))).astype(float) / 255.0

        # 중앙 50% 영역만 비교 (배경 노이즈 최소화)
        cy1, cy2, cx1, cx2 = 25, 75, 25, 75
        orig_c = orig_s[cy1:cy2, cx1:cx2]
        res_c = res_s[cy1:cy2, cx1:cx2]

        def _mean_sat(arr: "np.ndarray") -> float:
            cmax = arr.max(axis=2)
            cmin = arr.min(axis=2)
            return float(np.where(cmax > 0, (cmax - cmin) / cmax, 0).mean())

        orig_sat = _mean_sat(orig_c)
        res_sat = _mean_sat(res_c)

        # 원본이 무채색(흰/회색 견종)이면 체크 불필요
        if orig_sat < 0.08:
            return True

        # 결과 채도가 원본의 50% 미만이면 회색화 판정
        acceptable = res_sat >= orig_sat * 0.50
        logger.warning(
            "[gemini_pipeline] 색상 품질 — orig_sat=%.3f res_sat=%.3f → %s",
            orig_sat, res_sat, "OK" if acceptable else "FAIL(회색화 의심)",
        )
        return acceptable
    except Exception as exc:
        logger.warning("[gemini_pipeline] 색상 품질 체크 실패: %s — 통과 처리", exc)
        return True


async def _run_gemini(image_bytes: bytes, prompt: str, gemini_client) -> bytes:
    """Gemini로 이미지를 변환하고 결과 bytes를 반환한다.

    Args:
        image_bytes: 원본 이미지의 raw bytes
        prompt: 스타일 변환 프롬프트

    Returns:
        변환된 이미지 bytes

    Raises:
        RuntimeError: Gemini API가 이미지를 반환하지 않은 경우
    """
    logger.info("[gemini_pipeline] Gemini API 호출 시작 (model=%s)", _MODEL_GEMINI)

    mime_type = _detect_mime_type(image_bytes)
    logger.info("[gemini_pipeline] 감지된 MIME 타입: %s", mime_type)
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    text_part = prompt

    gemini_response = gemini_client.models.generate_content(
        model=_MODEL_GEMINI,
        contents=[image_part, text_part],
        config=GenerateContentConfig(
            response_modalities=["IMAGE"],
        ),
    )
    logger.info("[gemini_pipeline] Gemini API 응답 수신")

    candidates = gemini_response.candidates
    if not candidates:
        raise RuntimeError("Gemini API가 후보를 반환하지 않았습니다.")

    candidate = candidates[0]
    finish_reason = getattr(candidate, "finish_reason", None)
    logger.info("[gemini_pipeline] finish_reason: %s", finish_reason)

    if candidate.content is None:
        raise RuntimeError(
            f"Gemini API content가 None입니다 (finish_reason={finish_reason}). "
            "Safety filter 또는 이미지 변환 거부일 수 있습니다."
        )

    result_bytes: bytes | None = None
    for part in candidate.content.parts:
        if hasattr(part, "inline_data") and part.inline_data is not None:
            result_bytes = part.inline_data.data
            break

    if result_bytes is None:
        raise RuntimeError("Gemini API가 이미지를 반환하지 않았습니다.")

    logger.info("[gemini_pipeline] Gemini 이미지 추출 완료 (%d bytes)", len(result_bytes))
    return result_bytes


async def run_gemini_pipeline(
    image_url: str,
    breed_id: str,
    style_id: str,
    features_bbox: dict | None = None,
    meta_out: list[dict] | None = None,
) -> str:
    """
    Gemini를 사용해 강아지 사진을 그루밍 스타일로 변환한다.

    파이프라인 순서:
      1. 프롬프트 베이스 조회
      2. base64 dataURL이면 Cloudinary public URL로 교체
      3. 이미지 bytes 다운로드
      4. HEIC이면 JPEG 변환 후 Cloudinary 재업로드
      5. 특징 분석 + 눈/코 개별 bbox 탐지 (병렬)
      6. Gemini API 호출 (이미지 + 강화된 프롬프트)
      7. 색상 보정 (원본 털 위치 기준, 배경 보존)
      8. 눈·코 개별 합성 (주둥이 털은 미용 결과 유지)
      9. Cloudinary 업로드 후 URL 반환

    Args:
        image_url: 원본 강아지 이미지 URL (public URL 또는 data: 스킴)
        breed_id: 견종 ID (style_prompts.py 기준)
        style_id: 스타일 ID (해당 견종의 스타일)
        features_bbox: 레거시 단일 bbox (제공 시 face_parts 탐지 스킵 — 테스트 일관성 보장용)

    Returns:
        Cloudinary에 저장된 변환 결과 이미지 URL

    Raises:
        ValueError: 유효하지 않은 breed_id 또는 style_id
        RuntimeError: Gemini API 또는 Cloudinary 오류
    """
    # 1. 프롬프트 조회 — style_prompts.py가 유일한 데이터 소스
    prompt_data = get_prompt(breed_id, style_id)
    if prompt_data is None:
        raise ValueError(f"존재하지 않는 breed_id 또는 style_id: {breed_id}/{style_id}")

    base_prompt = prompt_data["prompt"]

    logger.info(
        "[gemini_pipeline] 파이프라인 시작 — breed=%s, style=%s", breed_id, style_id
    )

    # Gemini client를 한 번만 생성 — 모든 내부 함수에서 재사용
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

    try:
        # 2. base64 dataURL 감지 → Cloudinary public URL로 교체
        if image_url.startswith("data:"):
            logger.info("[gemini_pipeline] base64 dataURL → Cloudinary 업로드")
            upload_result = cloudinary.uploader.upload(
                image_url,
                folder="grooming-style/uploads",
                overwrite=True,
                resource_type="image",
                format="jpg",
            )
            image_url = upload_result["secure_url"]
            logger.info("[gemini_pipeline] Cloudinary 업로드 완료: %s", image_url)

        # 3. 이미지 bytes 다운로드 (Gemini API는 bytes 입력 필요)
        logger.info("[gemini_pipeline] 이미지 다운로드: %s", image_url)
        async with httpx.AsyncClient() as client:
            response = await client.get(image_url)
            response.raise_for_status()
            image_bytes = response.content
        logger.info("[gemini_pipeline] 이미지 다운로드 완료 (%d bytes)", len(image_bytes))

        # 4. HEIC → JPEG 변환 (Gemini는 HEIC 미지원)
        image_bytes, was_converted = _convert_to_jpeg_if_needed(image_bytes)
        if was_converted:
            logger.info("[gemini_pipeline] HEIC 변환 후 Cloudinary 재업로드")
            upload_result = cloudinary.uploader.upload(
                image_bytes,
                folder="grooming-style/uploads",
                overwrite=True,
                resource_type="image",
                format="jpg",
            )
            image_url = upload_result["secure_url"]
            logger.info("[gemini_pipeline] 변환 이미지 Cloudinary 업로드 완료: %s", image_url)

        # 5. 특징 분석 + 눈/코 개별 bbox 탐지 (병렬)
        #    features_bbox(레거시)가 외부 제공 시 → 색상 보정용으로만 사용, 합성은 스킵
        if features_bbox is not None:
            logger.info("[gemini_pipeline] 레거시 features_bbox 제공 — face_parts 탐지 스킵")
            analysis = await _analyze_dog_features(image_bytes, gemini_client)
            face_parts: list[dict] = []
        else:
            analysis, face_parts = await asyncio.gather(
                _analyze_dog_features(image_bytes, gemini_client),
                _detect_face_parts_bboxes(image_bytes, gemini_client),
            )
            if face_parts:
                logger.info("[gemini_pipeline] face parts 탐지 성공: %d개", len(face_parts))
            else:
                logger.warning("[gemini_pipeline] face parts 탐지 실패 — 합성 단계 스킵")

        # 5-1. 프롬프트 구성
        extracted_colors = _extract_dominant_fur_colors(image_bytes)
        if extracted_colors:
            logger.info("[gemini_pipeline] 추출된 털 색상: %s", extracted_colors)
            color_clause = (
                f"The dog's fur/coat color is extracted from the original photo as {extracted_colors}. "
                "Use EXACTLY these colors for the fur. Do NOT lighten, darken, or change the hue."
            )
        else:
            logger.warning("[gemini_pipeline] 털 색상 추출 실패 — 일반 색상 보존 지시 사용")
            color_clause = "Preserve the dog's exact original fur/coat color — do NOT change any color."

        # 견종명을 "this dog"으로 치환 — 견종명이 가진 암묵적 색상 연상(예: maltese=흰색)을 제거
        neutral_prompt = _BREED_NAMES_PATTERN.sub("this dog", base_prompt)
        logger.info("[gemini_pipeline] neutral_prompt: %s", neutral_prompt)

        gemini_prompt = (
            # 미용 적용을 첫 번째 지시로 배치 — Gemini가 "스타일 변환 작업"임을 첫 줄에서 인식
            "TASK: Apply the grooming style below to this dog photo.\n"
            "The dog's pose, position, and background must remain EXACTLY unchanged — only the fur/coat is modified.\n\n"
            f"GROOMING STYLE TO APPLY (shape/cut/texture only — color is handled separately):\n{neutral_prompt}\n\n"
            "GROOMING REQUIREMENTS:\n"
            "1. The grooming style MUST be clearly and visibly applied to the entire body.\n"
            "2. Change the fur cut shape, length, and texture according to the style above.\n"
            "3. The muzzle, beard, and face fur area must also match the grooming style.\n"
            "4. DO NOT change the dog's pose, body position, head angle, or background.\n"
            "5. DO NOT move the dog or alter its sitting/standing position in any way.\n\n"
            # 색상 규칙: 스타일 변경을 막지 않는다는 점을 명시
            f"COLOR RULE (applies only to fur/coat color — does NOT restrict the style change):\n"
            f"{color_clause}\n"
            "Any color word in the grooming style description must be IGNORED for color. "
            "Apply the shape and cut only; the color above takes precedence.\n\n"
            "FEATURES TO PRESERVE EXACTLY:\n"
            "- Eyes (shape, color, position): UNCHANGED\n"
            "- Nose (shape, color, position): UNCHANGED\n"
            "- Dog identity, face proportions, expression: UNCHANGED"
        )

        if analysis:
            enhanced_prompt = (
                f"{gemini_prompt}\n"
                f"- Original features: {analysis}"
            )
        else:
            enhanced_prompt = gemini_prompt

        # 6. Gemini API 호출 (색상 불량 + gating 합산 최대 2회 상한: retry_budget)
        retry_budget = 2
        gemini_calls = 1
        result_bytes = await _run_gemini(image_bytes, enhanced_prompt, gemini_client)
        if not _is_color_acceptable(image_bytes, result_bytes) and gemini_calls < retry_budget:
            logger.warning("[gemini_pipeline] 색상 품질 불량 — 재시도 (1회)")
            result_bytes = await _run_gemini(image_bytes, enhanced_prompt, gemini_client)
            gemini_calls += 1
            if _is_color_acceptable(image_bytes, result_bytes):
                logger.warning("[gemini_pipeline] 재시도 성공 — 색상 OK")
            else:
                logger.warning("[gemini_pipeline] 재시도 후에도 색상 불량 — 그대로 진행")

        # NOTE: _color_correct_result()는 비활성화 상태.
        # 히스토그램 LUT는 Gemini가 색을 크게 바꿀수록 역효과.
        # 색상 보존은 프롬프트(ABSOLUTE COLOR RULE + RGB 실측값) + 재시도로 대응.

        # 6-2. 결과 이미지에서 face parts 탐지 → 얼굴 파트 합성 (gating 포함)
        meta_list: list[dict] = []
        if face_parts:
            dst_parts = await _detect_face_parts_bboxes(result_bytes, gemini_client)
            if dst_parts:
                logger.info("[gemini_pipeline] 결과 이미지 face parts 탐지 성공: %d개", len(dst_parts))
            else:
                logger.warning("[gemini_pipeline] 결과 이미지 face parts 탐지 실패 — 비율 변환 fallback 사용")
            result_bytes, meta_list = _composite_face_parts(
                image_bytes, result_bytes, face_parts, dst_parts
            )
            logger.info("[gemini_pipeline] 얼굴 파트 합성 완료")

            # Gate-fail 재호출: mandatory(eyes + nose) 중 _MIN_MANDATORY_PARTS_OK 이상 skip
            mandatory = {"left_eye", "right_eye", "nose"}
            skipped_mandatory = {
                m["name"] for m in meta_list
                if m.get("skip_reason") and m["name"] in mandatory
            }
            if len(skipped_mandatory) >= _MIN_MANDATORY_PARTS_OK and gemini_calls < retry_budget:
                logger.warning(
                    "[gemini_pipeline] gate retry 발동 — skipped=%s (mandatory %d개 이상)",
                    skipped_mandatory, _MIN_MANDATORY_PARTS_OK,
                )
                result_bytes = await _run_gemini(image_bytes, enhanced_prompt, gemini_client)
                gemini_calls += 1
                dst_parts = await _detect_face_parts_bboxes(result_bytes, gemini_client)
                result_bytes, meta_list = _composite_face_parts(
                    image_bytes, result_bytes, face_parts, dst_parts
                )
                logger.info("[gemini_pipeline] gate retry 후 얼굴 파트 합성 재수행")

        # 7. Cloudinary 업로드
        logger.info("[gemini_pipeline] Cloudinary 업로드 시작")
        upload_result = cloudinary.uploader.upload(
            result_bytes,
            folder="grooming-results",
            resource_type="image",
        )
        result_url: str = upload_result["secure_url"]
        logger.info("[gemini_pipeline] Cloudinary 업로드 완료: %s", result_url)

        # meta_out 는 테스트/디버그용 side-channel (hot path 변경 없음)
        if meta_out is not None:
            meta_out.extend(meta_list)
            meta_out.append({"_pipeline": True, "gemini_calls": gemini_calls})

        return result_url

    except (ValueError, RuntimeError):
        raise
    except Exception as exc:
        logger.error(
            "[gemini_pipeline] 파이프라인 실패 (breed=%s, style=%s): %s",
            breed_id,
            style_id,
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"Gemini 파이프라인 처리 중 오류가 발생했습니다: {exc}") from exc
