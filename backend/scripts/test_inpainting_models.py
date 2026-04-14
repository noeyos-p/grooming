"""
Standalone async test script — SDXL inpainting candidate model comparison.

Tests 3 Replicate inpainting models to identify which one performs true
inpainting (preserves unmasked pixels exactly rather than using the mask
as a generation hint).

Usage:
    python backend/scripts/test_inpainting_models.py --image <url>

Requirements:
    pip install replicate cloudinary pillow httpx python-dotenv
"""

import argparse
import asyncio
import io
import json
import os
import sys
import time
from pathlib import Path

# Load .env from backend/.env before any other imports that read env vars.
# Path is resolved relative to this script's location (backend/scripts/).
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ENV_PATH = _BACKEND_DIR / ".env"

from dotenv import load_dotenv  # noqa: E402

load_dotenv(dotenv_path=_ENV_PATH)

import cloudinary  # noqa: E402
import cloudinary.uploader  # noqa: E402
import httpx  # noqa: E402
import replicate  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

# ---------------------------------------------------------------------------
# Cloudinary initialisation (same env-var pattern as ai_pipeline.py)
# ---------------------------------------------------------------------------
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.environ.get("CLOUDINARY_API_KEY", ""),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET", ""),
)

# ---------------------------------------------------------------------------
# Model configurations to test
# ---------------------------------------------------------------------------
MODEL_CONFIGS = [
    {
        # Replicate에서 직접 확인된 SDXL inpainting 모델 (lucataco — sdxl-controlnet과 동일 저자)
        "name": "lucataco/sdxl-inpainting",
        "id": "lucataco/sdxl-inpainting:a5b13068cc81a89a4fbeefeccc774869fcb34df4dbc92c1555e0f2771d49dde7",
        "mask_param": "mask",
        "extra": {"steps": 30},  # 이 모델은 num_inference_steps 대신 steps 사용
    },
    {
        # SD 1.5 inpainting — mask+invert_mask 지원, true inpainting 검증된 모델
        "name": "andreasjansson/sd-inpainting",
        "id": "andreasjansson/stable-diffusion-inpainting:e490d072a34a94a11e9711ed5a6ba621c3fab884eda1665d9d3a282d65a21180",
        "mask_param": "mask",
        "extra": {"num_inference_steps": 30},
    },
    {
        # SD 1.5 inpainting — stability-ai 공식 버전
        "name": "stability-ai/sd-inpainting",
        "id": "stability-ai/stable-diffusion-inpainting:95b7223104132402a9ae91cc677285bc5eb997834bd2349fa486f53910fd68b3",
        "mask_param": "mask",
        "extra": {"num_inference_steps": 30},
    },
]

PROMPT = (
    "maltese dog, teddy bear cut, fluffy white fur, groomed, "
    "professional dog grooming"
)
NEGATIVE_PROMPT = "deformed, blurry, bad anatomy, disfigured, low quality"

RESULTS_PATH = _BACKEND_DIR / "scripts" / "results.json"


# ---------------------------------------------------------------------------
# Mask generation
# ---------------------------------------------------------------------------

def _fetch_image_dimensions(image_url: str) -> tuple[int, int]:
    """Fetch the image from URL and return (width, height)."""
    with httpx.Client(timeout=30) as client:
        response = client.get(image_url)
        response.raise_for_status()
    img = Image.open(io.BytesIO(response.content))
    return img.size  # (width, height)


def _generate_mask_png(width: int, height: int) -> bytes:
    """Create a synthetic test mask as PNG bytes.

    Layout:
      - Black background  → preserve area (unmasked)
      - White circle in the center-top quadrant → transform area (simulates
        fur/body region, avoiding the face which typically sits higher)

    The circle is centered at (width/2, height * 0.35) with a radius of
    roughly min(width, height) * 0.25 so it scales naturally across image
    sizes.
    """
    mask = Image.new("L", (width, height), color=0)  # black background
    draw = ImageDraw.Draw(mask)

    cx = width // 2
    cy = int(height * 0.35)
    radius = int(min(width, height) * 0.25)

    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=255,  # white circle
    )

    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def _upload_mask_to_cloudinary(mask_bytes: bytes) -> str:
    """Upload mask PNG bytes to Cloudinary and return the public URL."""
    result = cloudinary.uploader.upload(
        mask_bytes,
        folder="grooming-style/test-masks",
        resource_type="image",
        format="png",
    )
    return result["secure_url"]


# ---------------------------------------------------------------------------
# Per-model test runner
# ---------------------------------------------------------------------------

async def _test_model(
    config: dict,
    image_url: str,
    mask_url: str,
) -> dict:
    """Run a single inpainting model and return a result record."""
    name = config["name"]
    model_id = config["id"]
    mask_param = config["mask_param"]
    extra = config.get("extra", {})

    print(f"  Testing {name} ...")

    start = time.monotonic()
    output_url: str | None = None
    error: str | None = None
    status = "error"

    try:
        model_input = {
            "image": image_url,
            mask_param: mask_url,
            "prompt": PROMPT,
            "negative_prompt": NEGATIVE_PROMPT,
            **extra,
        }
        output = await replicate.async_run(model_id, input=model_input)

        if isinstance(output, list):
            output_url = output[0] if output else None
        else:
            output_url = output

        status = "success"
        print(f"    -> success: {output_url}")

    except Exception as exc:
        error = str(exc)
        print(f"    -> error: {error}")

    elapsed = round(time.monotonic() - start, 2)

    return {
        "model": name,
        "status": status,
        "elapsed": elapsed,
        "output_url": output_url,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Results table printer
# ---------------------------------------------------------------------------

def _print_results_table(results: list[dict]) -> None:
    col_widths = {
        "model": max(len(r["model"]) for r in results),
        "status": 7,
        "elapsed": 8,
        "output_url": 60,
    }
    col_widths["model"] = max(col_widths["model"], len("Model"))

    header = (
        f"{'Model':<{col_widths['model']}}  "
        f"{'Status':<{col_widths['status']}}  "
        f"{'Time(s)':<{col_widths['elapsed']}}  "
        f"Output URL"
    )
    separator = "-" * len(header)

    print()
    print("=== Inpainting Model Test Results ===")
    print(separator)
    print(header)
    print(separator)

    for r in results:
        url_display = r["output_url"] or (f"ERROR: {r['error']}" if r["error"] else "N/A")
        # Truncate long URLs for table readability
        if len(url_display) > 80:
            url_display = url_display[:77] + "..."
        print(
            f"{r['model']:<{col_widths['model']}}  "
            f"{r['status']:<{col_widths['status']}}  "
            f"{r['elapsed']:<{col_widths['elapsed']}}  "
            f"{url_display}"
        )

    print(separator)
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test SDXL inpainting candidate models on Replicate."
    )
    parser.add_argument(
        "--image",
        required=False,
        metavar="URL",
        help="Public URL of the test dog image.",
    )
    args = parser.parse_args()

    if not args.image:
        print("Error: --image <url> is required.", file=sys.stderr)
        sys.exit(1)

    image_url: str = args.image

    # Validate that REPLICATE_API_TOKEN is present
    if not os.environ.get("REPLICATE_API_TOKEN"):
        print(
            "Error: REPLICATE_API_TOKEN is not set. Check backend/.env.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 1. Fetch image dimensions
    print(f"Fetching image dimensions from: {image_url}")
    try:
        width, height = _fetch_image_dimensions(image_url)
    except Exception as exc:
        print(f"Error: Could not fetch image — {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"  Image size: {width}x{height}")

    # 2. Generate and upload synthetic mask
    print("Generating synthetic test mask ...")
    mask_bytes = _generate_mask_png(width, height)
    print("Uploading mask to Cloudinary (grooming-style/test-masks) ...")
    try:
        mask_url = _upload_mask_to_cloudinary(mask_bytes)
    except Exception as exc:
        print(f"Error: Could not upload mask to Cloudinary — {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"  Mask URL: {mask_url}")

    # 3. Test each model in sequence (15s delay between calls to avoid 429)
    print()
    print("Running model tests in sequence (15s delay between models) ...")
    results: list[dict] = []
    for i, config in enumerate(MODEL_CONFIGS):
        if i > 0:
            print(f"  Waiting 15s before next model ...")
            await asyncio.sleep(15)
        result = await _test_model(config, image_url, mask_url)
        results.append(result)

    # 4. Print results table
    _print_results_table(results)

    # 5. Save full results to backend/scripts/results.json
    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"Full results saved to: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
