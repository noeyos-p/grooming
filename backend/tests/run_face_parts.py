"""
눈·코 탐지 + 윤곽 마스크 시각화 테스트.

실행:
  cd backend
  source venv/bin/activate
  python scripts/test_face_parts.py <이미지_경로>

결과물:
  /tmp/face_parts/
    annotated.jpg       — 원본에 bbox 표시
    <name>_crop.jpg     — 패딩 포함 크롭
    <name>_mask.jpg     — 윤곽 마스크
    <name>_blend.jpg    — 합성 미리보기 (흰 배경)
"""

import asyncio
import io
import json
import os
import re
import sys
from pathlib import Path

import pillow_heif
pillow_heif.register_heif_opener()

from dotenv import load_dotenv
from google import genai
from google.genai.types import Part
from PIL import Image, ImageDraw, ImageFilter
import numpy as np

load_dotenv(Path(__file__).parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).parent.parent))

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
_MODEL_ANALYSIS = "gemini-2.5-flash"

OUT_DIR = Path("/tmp/face_parts")
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── 탐지 ──────────────────────────────────────────────────────────────────────

def _parse_bbox(raw: str, name: str, img_w: int, img_h: int) -> dict | None:
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        return None
    try:
        data = json.loads(json_match.group())
    except Exception:
        return None
    d = data.get(name) or (data if {"xmin","ymin","xmax","ymax"}.issubset(data) else None)
    if not d:
        return None
    xmin, ymin = float(d["xmin"]), float(d["ymin"])
    xmax, ymax = float(d["xmax"]), float(d["ymax"])
    # 값이 100 초과이면 픽셀 좌표 or 0~1000 스케일로 판단
    max_val = max(xmin, ymin, xmax, ymax)
    if max_val <= 100:
        # 퍼센트
        return {
            "name": name,
            "xmin": int(xmin / 100 * img_w),
            "ymin": int(ymin / 100 * img_h),
            "xmax": int(xmax / 100 * img_w),
            "ymax": int(ymax / 100 * img_h),
        }
    elif max_val <= 1000:
        # 0~1000 스케일
        return {
            "name": name,
            "xmin": int(xmin / 1000 * img_w),
            "ymin": int(ymin / 1000 * img_h),
            "xmax": int(xmax / 1000 * img_w),
            "ymax": int(ymax / 1000 * img_h),
        }
    else:
        # 직접 픽셀 좌표
        return {
            "name": name,
            "xmin": int(xmin),
            "ymin": int(ymin),
            "xmax": int(xmax),
            "ymax": int(ymax),
        }


async def detect_face_parts(image_bytes: bytes, client) -> list[dict]:
    img = Image.open(io.BytesIO(image_bytes))
    img_w, img_h = img.size

    # HEIC → JPEG 변환
    mime = "image/jpeg"
    if image_bytes[:4] == b"\x89PNG":
        mime = "image/png"
    elif len(image_bytes) >= 12 and image_bytes[4:8] == b"ftyp":
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=95)
        image_bytes = buf.getvalue()
        mime = "image/jpeg"

    image_part = Part.from_bytes(data=image_bytes, mime_type=mime)

    # ── CALL 1: 코 탐지 ────────────────────────────────────────────────────────
    nose_prompt = (
        "In this dog photo, find the dog's NOSE.\n"
        "The nose is the small DARK oval/heart-shaped bump at the very tip of the snout/muzzle.\n"
        "It is the darkest feature on the face — distinctly darker than the surrounding fur.\n"
        "The box must tightly wrap ONLY the dark nose leather. Do NOT include muzzle fur.\n"
        'Return ONLY: {"nose": {"xmin": 0-100, "ymin": 0-100, "xmax": 0-100, "ymax": 0-100}}\n'
        "Values = % of image dimensions."
    )
    r1 = await asyncio.to_thread(
        client.models.generate_content,
        model=_MODEL_ANALYSIS,
        contents=[image_part, Part.from_text(text=nose_prompt)],
    )
    raw1 = r1.text or ""
    print(f"[코 탐지 raw] {raw1[:300]}")
    nose_part = _parse_bbox(raw1, "nose", img_w, img_h)
    if nose_part:
        nose_ymin_pct = nose_part["ymin"] / img_h * 100
        print(f"  nose: {nose_part}  ({nose_part['xmax']-nose_part['xmin']}×{nose_part['ymax']-nose_part['ymin']}px)  ymin={nose_ymin_pct:.1f}%")
    else:
        nose_ymin_pct = 50.0
        print("  [WARN] 코 탐지 실패")

    # ── CALL 2: 눈 탐지 (코 위치 기준 constraint 포함) ─────────────────────────
    eye_ymax_pct = max(5.0, nose_ymin_pct - 2.0)
    eye_ymin_pct = max(0.0, nose_ymin_pct - 20.0)  # 코보다 20% 이상 위는 이마 → 제외
    eye_prompt = (
        f"In this dog photo, find the TWO EYES.\n"
        f"The eyes are small dark circles, partially hidden by fur.\n"
        f"They must be in the VERTICAL RANGE {eye_ymin_pct:.0f}%–{eye_ymax_pct:.0f}% from the top of the image.\n"
        f"  - Above {eye_ymin_pct:.0f}% is the forehead/fur — NOT the eyes.\n"
        f"  - Below {eye_ymax_pct:.0f}% is the nose area — NOT the eyes.\n"
        f"The eyes are the two dark CIRCULAR features visible between that range.\n"
        f"- left_eye: dog's left eye = RIGHT side of photo\n"
        f"- right_eye: dog's right eye = LEFT side of photo\n"
        f"Each box: no wider than 8%, no taller than 8% of image.\n"
        'Return ONLY: {"left_eye": {"xmin": 0-100, "ymin": 0-100, "xmax": 0-100, "ymax": 0-100}, '
        '"right_eye": {"xmin": 0-100, "ymin": 0-100, "xmax": 0-100, "ymax": 0-100}}\n'
        "Values = % of image dimensions."
    )
    r2 = await asyncio.to_thread(
        client.models.generate_content,
        model=_MODEL_ANALYSIS,
        contents=[image_part, Part.from_text(text=eye_prompt)],
    )
    raw2 = r2.text or ""
    print(f"[눈 탐지 raw] {raw2[:300]}")

    parts = []
    if nose_part:
        parts.append(nose_part)
    for name in ("left_eye", "right_eye"):
        p = _parse_bbox(raw2, name, img_w, img_h)
        if p:
            parts.append(p)
            print(f"  {name}: {p}  ({p['xmax']-p['xmin']}×{p['ymax']-p['ymin']}px)")
        else:
            print(f"  [WARN] {name} 탐지 실패")
    return parts


# ── 마스크 ─────────────────────────────────────────────────────────────────────

def create_contour_mask(crop_img: Image.Image, crop_w: int, crop_h: int, name: str) -> Image.Image:
    """
    절대 밝기 임계값으로 어두운 특징 픽셀을 감지한 뒤,
    크롭 중앙 1/2 영역에 해당하는 픽셀만 유효 처리 → 주변 털/그림자 배제.

    - 절대 임계값 < 80: 진짜 어두운 픽셀만 (눈동자·코 가죽)
    - 중앙 마스크: 크롭 크기의 75% 영역만 유효 (테두리 털 배제)
    - MaxFilter 팽창 → GaussianBlur 페더링
    """
    gray = np.array(crop_img.convert("L"), dtype=np.float32)
    gray_min = float(gray.min())
    p5  = float(np.percentile(gray, 5))

    # 밝기 분포 출력 (디버그)
    print(f"    [{name}] 밝기 분포: min={gray_min:.0f}  p5={p5:.0f}  p10={np.percentile(gray,10):.0f}"
          f"  p25={np.percentile(gray,25):.0f}  p50={np.percentile(gray,50):.0f}"
          f"  max={gray.max():.0f}")

    # 적응형 임계값: p5 + 30
    # min 대신 p5 사용: min은 패딩 모서리의 단일 이상치 픽셀일 수 있어 thresh가 너무 낮아짐
    thresh = min(p5 + 30.0, 120.0)
    dark_mask = (gray <= thresh).astype(np.uint8) * 255

    # 크롭 중앙 60% 영역만 유효 (사방 20% 테두리 = 패딩 포함 테두리 털 배제)
    margin_x = int(crop_w * 0.20)
    margin_y = int(crop_h * 0.20)
    center_mask = np.zeros_like(dark_mask)
    center_mask[margin_y: crop_h - margin_y, margin_x: crop_w - margin_x] = 255

    combined = ((dark_mask > 0) & (center_mask > 0)).astype(np.uint8) * 255

    dark_ratio = combined.sum() / 255 / (crop_w * crop_h)
    print(f"    [{name}] thresh={thresh:.0f}  어두운 픽셀(중앙 60%): {dark_ratio:.1%}")

    if dark_ratio < 0.02:
        # 어두운 픽셀 거의 없음 (bbox가 털에 잡혔거나 특징이 매우 작음) → 중앙 타원 폴백
        print(f"    [{name}] 어두운 픽셀 부족 → 중앙 타원 폴백")
        mask_img = Image.new("L", (crop_w, crop_h), 0)
        m = int(max(crop_w, crop_h) * 0.15)
        ImageDraw.Draw(mask_img).ellipse([m, m, crop_w - m - 1, crop_h - m - 1], fill=255)
    else:
        mask_img = Image.fromarray(combined, "L")
        # 소형 팽창으로 특징 내부 채우기 (과도한 확장 방지)
        dilation = max(3, int(max(crop_w, crop_h) * 0.07))
        if dilation % 2 == 0:
            dilation += 1
        mask_img = mask_img.filter(ImageFilter.MaxFilter(dilation))

    blur_r = max(3, int(max(crop_w, crop_h) * 0.07))
    mask_img = mask_img.filter(ImageFilter.GaussianBlur(radius=blur_r))
    return mask_img


# ── 시각화 ────────────────────────────────────────────────────────────────────

COLORS = {
    "left_eye":  (255, 80,  80),
    "right_eye": (80,  220, 80),
    "nose":      (80,  80,  255),
}

def save_annotated(image: Image.Image, parts: list[dict]):
    ann = image.copy().convert("RGB")
    draw = ImageDraw.Draw(ann)
    for p in parts:
        color = COLORS.get(p["name"], (255, 255, 0))
        draw.rectangle([p["xmin"], p["ymin"], p["xmax"], p["ymax"]], outline=color, width=6)
        draw.text((p["xmin"], max(0, p["ymin"] - 30)), p["name"], fill=color)
    path = OUT_DIR / "annotated.jpg"
    ann.save(path, quality=90)
    print(f"[저장] {path}")


def save_part_previews(image: Image.Image, parts: list[dict], padding_ratio: float = 0.06):
    orig_w, orig_h = image.size
    print()
    for p in parts:
        fw = p["xmax"] - p["xmin"]
        fh = p["ymax"] - p["ymin"]
        pad = int(max(fw, fh) * padding_ratio)
        ox1 = max(0, p["xmin"] - pad)
        oy1 = max(0, p["ymin"] - pad)
        ox2 = min(orig_w, p["xmax"] + pad)
        oy2 = min(orig_h, p["ymax"] + pad)

        if ox2 <= ox1 or oy2 <= oy1:
            print(f"  [{p['name']}] 크롭 범위 오류 — 스킵")
            continue
        crop = image.crop((ox1, oy1, ox2, oy2)).convert("RGB")
        cw, ch = crop.size

        mask = create_contour_mask(crop, cw, ch, p["name"])

        # 크롭 원본 (4× 확대해서 저장 — 소형 눈도 잘 보이게)
        scale = max(1, 200 // min(cw, ch))
        crop_big = crop.resize((cw * scale, ch * scale), Image.NEAREST)
        crop_path = OUT_DIR / f"{p['name']}_crop.jpg"
        crop_big.save(crop_path, quality=95)

        mask_big = mask.resize((cw * scale, ch * scale), Image.NEAREST)
        mask_path = OUT_DIR / f"{p['name']}_mask.jpg"
        mask_big.save(mask_path, quality=95)

        # 블렌드 미리보기: 흰 배경에 마스크 적용
        bg = Image.new("RGB", (cw, ch), (255, 255, 255))
        bg.paste(crop, mask=mask)
        bg_big = bg.resize((cw * scale, ch * scale), Image.NEAREST)
        blend_path = OUT_DIR / f"{p['name']}_blend.jpg"
        bg_big.save(blend_path, quality=95)

        print(f"  [{p['name']}] crop={cw}×{ch} (×{scale}) → {blend_path.name}")


# ── 메인 ──────────────────────────────────────────────────────────────────────

async def main():
    if len(sys.argv) < 2:
        print("Usage: python test_face_parts.py <image_path>")
        sys.exit(1)

    img_path = Path(sys.argv[1])
    image_bytes = img_path.read_bytes()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    print(f"[이미지] {img_path.name}  {image.size}")

    client = genai.Client(api_key=GOOGLE_API_KEY)

    print("\n[1] 얼굴 파트 탐지 중...")
    parts = await detect_face_parts(image_bytes, client)
    print(f"  탐지된 파트: {len(parts)}개")

    if not parts:
        print("[FAIL] 탐지 결과 없음")
        return

    print("\n[2] 시각화 저장 중...")
    save_annotated(image, parts)
    save_part_previews(image, parts)

    print(f"\n[완료] 결과: {OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
