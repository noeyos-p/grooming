"""
눈/코 원본 보존 검증 스크립트.

1. 로컬 이미지를 Cloudinary에 업로드
2. run_gemini_pipeline 실행
3. 원본 이미지와 결과 이미지의 눈/코 영역 픽셀 차이(MAE)를 계산
4. 결과 비교 이미지를 /tmp/face_test_result.jpg 로 저장

Usage:
    cd backend
    source venv/bin/activate
    python scripts/test_face_preservation.py --image ~/Downloads/IMG_7641.jpg
"""

import argparse
import asyncio
import io
import os
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_BACKEND_DIR / ".env")

import cloudinary
import cloudinary.uploader
import httpx
from PIL import Image, ImageDraw, ImageFont

cloudinary.config(
    cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key=os.environ["CLOUDINARY_API_KEY"],
    api_secret=os.environ["CLOUDINARY_API_SECRET"],
)

# HEIC 지원
import pillow_heif
pillow_heif.register_heif_opener()

from services.image_utils import _convert_to_jpeg_if_needed
from services.gemini_pipeline import (
    run_gemini_pipeline,
    _detect_face_parts_bboxes,
)
from google import genai


BREED_ID = "maltese"
STYLE_ID = "teddy_cut"
OUTPUT_PATH = Path("/tmp/face_test_result.jpg")
THRESHOLD_MAE = 25.0  # 픽셀 평균 절대오차 기준 (0~255), 이 이하면 보존 성공

_CHANGELOG_PATH = _BACKEND_DIR / "CHANGELOG.md"


def _append_url_to_changelog(
    image_url: str,
    result_url: str,
    mae: float,
    status: str,
    image_path: str,
) -> None:
    """테스트 결과 URL을 backend/CHANGELOG.md 오늘 날짜 섹션에 기록한다."""
    from datetime import datetime

    if not _CHANGELOG_PATH.exists():
        return

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y-%m-%d %H:%M")
    entry = (
        f"\n**[face-preservation] {timestamp}** — `{Path(image_path).name}` / `{BREED_ID}/{STYLE_ID}`\n"
        f"- 업로드: {image_url}\n"
        f"- 결과: {result_url}\n"
        f"- MAE: {mae:.1f} / 기준 {THRESHOLD_MAE} [{status}]\n"
    )

    content = _CHANGELOG_PATH.read_text(encoding="utf-8")
    date_marker = f"## {today}"
    date_idx = content.find(date_marker)

    if date_idx == -1:
        # 오늘 날짜 섹션 없으면 첫 번째 ## 날짜 앞에 새 섹션 생성
        first_date_idx = content.find("\n## ")
        if first_date_idx == -1:
            _CHANGELOG_PATH.write_text(content + f"\n## {today}\n" + entry, encoding="utf-8")
        else:
            new_content = content[:first_date_idx] + f"\n\n## {today}\n" + entry + content[first_date_idx:]
            _CHANGELOG_PATH.write_text(new_content, encoding="utf-8")
        return

    # 오늘 날짜 섹션 다음 첫 번째 ### 앞에 삽입
    after_date = date_idx + len(date_marker)
    next_phase_idx = content.find("\n### ", after_date)
    if next_phase_idx == -1:
        _CHANGELOG_PATH.write_text(content + entry, encoding="utf-8")
    else:
        new_content = content[:next_phase_idx] + entry + content[next_phase_idx:]
        _CHANGELOG_PATH.write_text(new_content, encoding="utf-8")

    print(f"  CHANGELOG 기록 완료: {_CHANGELOG_PATH}")


def _compute_face_mae(original: Image.Image, result: Image.Image, bboxes: list[dict]) -> float:
    """눈/코 bbox 영역들의 평균 절대오차(MAE)를 계산한다. 0에 가까울수록 보존 성공.

    bboxes: [{"name": str, "xmin": px, ...}, ...] — 개별 파트 리스트
    각 파트별 MAE를 구한 뒤 평균 반환.
    """
    import numpy as np

    orig_w, orig_h = original.size
    res_w, res_h = result.size
    maes = []

    for bbox in bboxes:
        rx1 = round(bbox["xmin"] / orig_w * res_w)
        ry1 = round(bbox["ymin"] / orig_h * res_h)
        rx2 = round(bbox["xmax"] / orig_w * res_w)
        ry2 = round(bbox["ymax"] / orig_h * res_h)

        if rx2 <= rx1 or ry2 <= ry1:
            continue

        orig_crop = original.crop((bbox["xmin"], bbox["ymin"], bbox["xmax"], bbox["ymax"])).convert("RGB")
        result_crop = result.crop((rx1, ry1, rx2, ry2)).convert("RGB")

        if orig_crop.size != result_crop.size:
            result_crop = result_crop.resize(orig_crop.size, Image.LANCZOS)

        orig_arr = np.array(orig_crop, dtype=float)
        res_arr = np.array(result_crop, dtype=float)
        maes.append(float(abs(orig_arr - res_arr).mean()))

    return float(sum(maes) / len(maes)) if maes else 999.0


def _save_comparison(
    original: Image.Image,
    result: Image.Image,
    bboxes: list[dict],
    mae: float,
    output_path: Path,
) -> None:
    """원본 / 결과 / 눈+코 크롭들 비교 이미지를 저장한다."""
    TARGET_H = 500
    def _resize_h(img: Image.Image, h: int) -> Image.Image:
        ratio = h / img.height
        return img.resize((int(img.width * ratio), h), Image.LANCZOS)

    orig_r = _resize_h(original.convert("RGB"), TARGET_H)
    res_r = _resize_h(result.convert("RGB"), TARGET_H)

    # 각 파트 크롭 (원본)
    part_crops = []
    for bbox in bboxes:
        crop = original.crop((bbox["xmin"], bbox["ymin"], bbox["xmax"], bbox["ymax"])).convert("RGB")
        part_crops.append((bbox["name"], _resize_h(crop, 150)))

    crop_total_w = sum(c.width for _, c in part_crops) + max(0, len(part_crops) - 1) * 5

    total_w = orig_r.width + res_r.width + crop_total_w + 20 + len(part_crops) * 5
    canvas = Image.new("RGB", (total_w, TARGET_H + 40), (30, 30, 30))
    canvas.paste(orig_r, (0, 40))
    canvas.paste(res_r, (orig_r.width + 10, 40))

    x_offset = orig_r.width + res_r.width + 20
    for name, crop in part_crops:
        canvas.paste(crop, (x_offset, 40))
        draw_tmp = ImageDraw.Draw(canvas)
        draw_tmp.text((x_offset + 2, 42), name, fill=(255, 200, 50))
        x_offset += crop.width + 5

    draw = ImageDraw.Draw(canvas)
    status = "PASS" if mae < THRESHOLD_MAE else "FAIL"
    color = (80, 200, 80) if status == "PASS" else (220, 60, 60)
    draw.text((4, 4), "Original", fill=(200, 200, 200))
    draw.text((orig_r.width + 14, 4), "Result", fill=(200, 200, 200))
    draw.text((orig_r.width + res_r.width + 24, 4), "Parts (orig)", fill=(200, 200, 200))
    draw.text((total_w // 2 - 100, 18), f"Parts MAE: {mae:.1f}  [{status}]", fill=color)

    canvas.save(output_path, quality=92)
    print(f"  비교 이미지 저장: {output_path}")


async def main(image_path: str) -> None:
    path = Path(image_path).expanduser()
    if not path.exists():
        print(f"ERROR: 파일 없음 — {path}", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== 얼굴 보존 테스트 시작 ===")
    print(f"  입력 이미지: {path}")
    print(f"  견종/스타일: {BREED_ID}/{STYLE_ID}")

    # 1. HEIC 변환 + Cloudinary 업로드
    raw_bytes = path.read_bytes()
    jpeg_bytes, was_converted = _convert_to_jpeg_if_needed(raw_bytes)
    if was_converted:
        print("  HEIC → JPEG 변환 완료")

    print("  Cloudinary 업로드 중...")
    upload_result = cloudinary.uploader.upload(
        jpeg_bytes,
        folder="grooming-style/uploads",
        resource_type="image",
        format="jpg",
    )
    image_url = upload_result["secure_url"]
    print(f"  업로드 완료: {image_url}")

    # 2. 원본 이미지 열기
    original_img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    print(f"  원본 이미지 크기: {original_img.size}")

    # 3. 눈/코 개별 bbox 탐지 (MAE 측정용)
    print("  눈/코 bbox 탐지 중...")
    gemini_client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    face_parts = await _detect_face_parts_bboxes(jpeg_bytes, gemini_client)
    img_w, img_h = original_img.size

    if face_parts:
        for p in face_parts:
            area_p = (p["xmax"]-p["xmin"]) * (p["ymax"]-p["ymin"]) / (img_w * img_h)
            print(f"  [{p['name']}] bbox={p['xmin']},{p['ymin']},{p['xmax']},{p['ymax']}  ({area_p:.1%})")
        # MAE 측정용: 개별 파트 합산 평균 사용
        face_bbox = face_parts  # 파트 리스트 전달
    else:
        print("  bbox 탐지 실패 — 전체 이미지로 MAE 계산")
        face_bbox = [{"xmin": 0, "ymin": 0, "xmax": img_w, "ymax": img_h, "name": "full"}]

    # 4. Gemini 파이프라인 실행 (파이프라인 내부에서 독립적으로 bbox 탐지 + 합성)
    print(f"\n  Gemini 파이프라인 실행 중...")
    start = time.monotonic()
    try:
        result_url = await run_gemini_pipeline(
            image_url, BREED_ID, STYLE_ID
        )
    except Exception as e:
        print(f"  ERROR: 파이프라인 실패 — {e}")
        sys.exit(1)
    elapsed = time.monotonic() - start
    print(f"  완료 ({elapsed:.1f}s): {result_url}")

    # 5. 결과 이미지 다운로드
    print("  결과 이미지 다운로드 중...")
    async with httpx.AsyncClient() as client:
        resp = await client.get(result_url)
        resp.raise_for_status()
        result_bytes = resp.content
    result_img = Image.open(io.BytesIO(result_bytes)).convert("RGB")
    print(f"  결과 이미지 크기: {result_img.size}")

    # 6. 눈/코 영역 MAE 계산 (개별 파트 평균)
    mae = _compute_face_mae(original_img, result_img, face_bbox)
    status = "PASS" if mae < THRESHOLD_MAE else "FAIL"
    print(f"\n  Parts MAE (눈/코 개별 평균): {mae:.1f} / 기준: {THRESHOLD_MAE}  → [{status}]")

    # 7. 비교 이미지 저장
    _save_comparison(original_img, result_img, face_bbox, mae, OUTPUT_PATH)

    print(f"\n  결과 URL: {result_url}")
    print(f"  비교 이미지: {OUTPUT_PATH}")
    if status == "PASS":
        print("  SUCCESS: 얼굴 보존 성공")
    else:
        print("  FAIL: 얼굴 변형 있음 — 추가 수정 필요")

    # CHANGELOG에 URL 기록
    _append_url_to_changelog(image_url, result_url, mae, status, image_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="테스트 이미지 경로")
    args = parser.parse_args()
    asyncio.run(main(args.image))
