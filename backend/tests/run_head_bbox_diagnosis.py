"""
Head bbox 재탐지 실패 원인 분석 — Phase 34 v1.1 진단 스크립트.

배치 실측(N=16)에서 `_detect_full_head_bbox()`가 결과 이미지에 대해 15/16 실패.
원본 이미지는 100% 성공, 결과 이미지만 실패하는 패턴을 재현·진단한다.

가설:
  H1. 결과 JPEG 포맷/MIME 감지 문제
  H2. 스타일화된 털(페인터리/만화풍)로 VLM이 dog head 인식 실패
  H3. VLM이 prose 섞인 응답을 반환 → JSON 파싱 실패
  H4. 단순 rate limiting

진단 방법:
  - 배치 결과 URL 4장(anchor 2, gemini 2 — 실패1+성공1) 다운로드
  - `_detect_full_head_bbox()` 원형 호출하여 raw response 로그
  - MIME 감지 결과 기록
  - 실패/성공 원문을 stdout JSON으로 dump

사용:
  cd backend && source venv/bin/activate
  python tests/run_head_bbox_diagnosis.py
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai.types import Part
from PIL import Image

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
load_dotenv(_BACKEND_ROOT / ".env")

from services.image_utils import _detect_mime_type  # noqa: E402
from services.inpaint_pipeline import _detect_full_head_bbox  # noqa: E402

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
_MODEL = "gemini-2.5-flash"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("head_bbox_diag")


SAMPLES = [
    {
        "label": "anchor/maltese/teddy_cut (iou_fail)",
        "url": "https://res.cloudinary.com/dubnzx8ew/image/upload/v1776833654/grooming-results/xws4k5coiciqyxm02nrm.jpg",
    },
    {
        "label": "anchor/pomeranian/bear_cut (iou_fail)",
        "url": "https://res.cloudinary.com/dubnzx8ew/image/upload/v1776834062/grooming-results/dvqa63r0lb4f6zu4t6s4.jpg",
    },
    {
        "label": "gemini/maltese/teddy_cut (iou_fail)",
        "url": "https://res.cloudinary.com/dubnzx8ew/image/upload/v1776834763/grooming-results/axmewijuzufrccnp6vx3.jpg",
    },
    {
        "label": "gemini/shih_tzu/puppy_cut (SUCCESS — iou=0.4892)",
        "url": "https://res.cloudinary.com/dubnzx8ew/image/upload/v1776835333/grooming-results/gdjqovlymoriyxskgbgt.jpg",
    },
]

ORIGINAL_PROMPT = (
    "Find the bounding box that covers the dog's ENTIRE HEAD in this image.\n"
    "The box must include: top of the skull, both ears, eyes, nose, mouth, "
    "tongue (if visible), and below the chin.\n"
    "Make sure the bottom edge is BELOW the tongue tip if the tongue is out.\n"
    'Return ONLY this JSON, no other text:\n'
    '{"xmin": 0-100, "ymin": 0-100, "xmax": 0-100, "ymax": 0-100}\n'
    "Values are percentages: 0=top-left corner, 100=bottom-right corner."
)


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=90.0)
        r.raise_for_status()
        return r.content


async def _call_gemini(image_bytes: bytes, prompt: str) -> tuple[str, dict]:
    """원형 호출 — raw response와 meta 반환."""
    mime = _detect_mime_type(image_bytes)
    img = Image.open(io.BytesIO(image_bytes))
    img_w, img_h = img.size

    client = genai.Client(api_key=GOOGLE_API_KEY)
    image_part = Part.from_bytes(data=image_bytes, mime_type=mime)
    text_part = Part.from_text(text=prompt)

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=_MODEL,
        contents=[image_part, text_part],
    )
    raw = response.text or ""
    return raw, {
        "mime": mime,
        "image_size": [img_w, img_h],
        "bytes_len": len(image_bytes),
    }


def _parse(raw: str) -> dict | None:
    m = re.search(r"\{[\s\S]*?\}", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    required = {"xmin", "ymin", "xmax", "ymax"}
    if not required.issubset(data.keys()):
        return None
    return data


async def _diagnose_one(label: str, url: str) -> dict:
    logger.info("=== %s ===", label)
    image_bytes = await _download(url)

    raw, meta = await _call_gemini(image_bytes, ORIGINAL_PROMPT)
    parsed = _parse(raw)

    # 프로덕션 함수 경로 — 새 파서(_parse_head_bbox_payload) 포함
    prod_client = genai.Client(api_key=GOOGLE_API_KEY)
    prod_bbox = await _detect_full_head_bbox(image_bytes, prod_client)

    return {
        "label": label,
        "url": url,
        "meta": meta,
        "raw_response": raw,
        "raw_length": len(raw),
        "parsed_legacy_xyxy_only": parsed,
        "legacy_parse_success": parsed is not None,
        "production_bbox": prod_bbox,
        "production_success": prod_bbox is not None,
    }


async def main_async() -> None:
    if not GOOGLE_API_KEY:
        raise SystemExit("GOOGLE_API_KEY 미설정")
    results = []
    for s in SAMPLES:
        try:
            r = await _diagnose_one(s["label"], s["url"])
        except Exception as exc:  # noqa: BLE001
            r = {"label": s["label"], "url": s["url"], "error": type(exc).__name__, "reason": str(exc)}
        results.append(r)

    print(json.dumps({"samples": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main_async())
