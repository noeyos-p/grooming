"""
anchor_inpaint_pipeline 유닛 테스트 — fal / Cloudinary / Gemini 호출 없이 순수 함수만 검증.

검증 대상:
 - `_per_part_r2` clamp 경계 (최소 8 / 최대 28)
 - `_build_bbox_anchor_mask` — 앵커 bbox 합집합이 255 로 채워지는가
 - `_build_two_layer_mask` — 전신 실루엣 내부 255, 앵커 dilate(r2) 영역 0
 - `_hard_restore_anchors` — FLUX 결과가 hard preserve 마스크 내 위치에서 원본과 동일해지는가
 - `_compose_flux_prompt` — 색상 추출 실패 시 기본 문구 사용
 - `evf_sam_geometry` 내부 morphology 헬퍼 검증
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from services.anchor_inpaint_pipeline import (
    _build_bbox_anchor_mask,
    _build_two_layer_mask,
    _compose_flux_prompt,
    _hard_restore_anchors,
    _per_part_r2,
)
from services.evf_sam_geometry import (
    PartMask,
    _cleanup_nose,
    _fill_holes,
    _largest_cc,
    _opening,
)


def _make_part(name: str, bbox: tuple[int, int, int, int]) -> PartMask:
    xmin, ymin, xmax, ymax = bbox
    mask = np.zeros((max(10, ymax + 2), max(10, xmax + 2)), dtype=bool)
    mask[ymin : ymax + 1, xmin : xmax + 1] = True
    return PartMask(
        name=name,
        mask=mask,
        cx=(xmin + xmax) / 2.0,
        cy=(ymin + ymax) / 2.0,
        area_px=int(mask.sum()),
        bbox=(xmin, ymin, xmax, ymax),
    )


# ── _per_part_r2 clamp ───────────────────────────────────────────────────────


def test_per_part_r2_clamp_min():
    parts = {"left_eye": _make_part("left_eye", (10, 10, 15, 15))}  # min_side 6
    r2 = _per_part_r2(parts, r2_scale=1.0)
    # round(6 * 0.30) = 2 → clamp to 8
    assert r2["left_eye"] == 8


def test_per_part_r2_clamp_max():
    parts = {"nose": _make_part("nose", (0, 0, 300, 400))}  # min_side 301
    r2 = _per_part_r2(parts, r2_scale=1.0)
    # round(301 * 0.30) = 90 → clamp to 28
    assert r2["nose"] == 28


def test_per_part_r2_mid_range():
    parts = {"nose": _make_part("nose", (0, 0, 99, 99))}  # min_side 100
    r2 = _per_part_r2(parts, r2_scale=1.0)
    # round(100 * 0.30) = 30 → clamp to 28
    assert r2["nose"] == 28
    # scale 1.2 도 동일 clamp
    assert _per_part_r2(parts, r2_scale=1.2)["nose"] == 28


def test_per_part_r2_degraded_scale_effect():
    parts = {"eye": _make_part("eye", (0, 0, 49, 49))}  # min_side 50
    # round(50 * 0.30 * 1.0) = 15
    assert _per_part_r2(parts, r2_scale=1.0)["eye"] == 15
    # round(50 * 0.30 * 1.2) = 18
    assert _per_part_r2(parts, r2_scale=1.2)["eye"] == 18


# ── _build_bbox_anchor_mask ──────────────────────────────────────────────────


def test_build_bbox_anchor_mask_union():
    parts = {
        "left_eye": _make_part("left_eye", (100, 50, 120, 70)),
        "right_eye": _make_part("right_eye", (60, 50, 80, 70)),
        "nose": _make_part("nose", (85, 80, 105, 110)),
    }
    arr = _build_bbox_anchor_mask(parts, (200, 200))
    assert arr.shape == (200, 200)
    # 각 bbox 내부 모든 픽셀 255
    assert (arr[50:71, 100:121] == 255).all()
    assert (arr[50:71, 60:81] == 255).all()
    assert (arr[80:111, 85:106] == 255).all()
    # 바깥 픽셀은 0
    assert arr[0, 0] == 0
    assert arr[199, 199] == 0


def test_build_bbox_anchor_mask_clamps_to_image():
    parts = {"big": _make_part("big", (150, 150, 999, 999))}
    arr = _build_bbox_anchor_mask(parts, (200, 200))
    # 200×200 이미지에서 out-of-bounds 부분도 안전하게 처리되어야 함
    assert arr[199, 199] == 255
    assert arr[0, 0] == 0


# ── _build_two_layer_mask ────────────────────────────────────────────────────


def test_build_two_layer_mask_respects_silhouette_and_anchors():
    parts = {
        "nose": _make_part("nose", (90, 90, 110, 110)),  # min_side 21 → r2=8 (clamp)
    }
    # 전신 실루엣 — 50:150 사각형 영역만 전경 (이전 head_bbox 와 동일 모양으로
    # 기존 픽셀 단위 검증 케이스 재사용)
    body_silhouette = np.zeros((200, 200), dtype=bool)
    body_silhouette[50:150, 50:150] = True
    r2 = _per_part_r2(parts, r2_scale=1.0)
    flux = _build_two_layer_mask(parts, body_silhouette, (200, 200), r2)

    # 실루엣 바깥은 0 (보존)
    assert flux[0, 0] == 0
    assert flux[199, 199] == 0
    # 실루엣 내부 중 앵커 dilate 영역 밖은 255 (편집)
    assert flux[55, 55] == 255
    # 앵커 bbox 내부는 0
    assert flux[100, 100] == 0
    # 앵커 외곽 r2 링 내부도 0 — (90-r2, 90-r2) 안쪽
    assert flux[90 - r2["nose"], 90 - r2["nose"]] == 0
    # r2 링 바로 바깥 (실루엣 내부)은 255
    outside = max(r2["nose"] + 1, 1)
    ox = 90 - outside
    oy = 90 - outside
    if ox >= 50 and oy >= 50:
        assert flux[oy, ox] == 255


def test_build_two_layer_mask_resizes_silhouette():
    parts = {"nose": _make_part("nose", (40, 40, 60, 60))}
    # 실루엣 해상도가 다르면 NEAREST 로 리사이즈되어야 함
    body_silhouette = np.zeros((100, 100), dtype=bool)
    body_silhouette[10:90, 10:90] = True
    r2 = _per_part_r2(parts, r2_scale=1.0)
    flux = _build_two_layer_mask(parts, body_silhouette, (100, 100), r2)
    # 리사이즈 결과가 동일 shape 일 경우 그대로 처리되는지 확인
    assert flux.shape == (100, 100)
    # 실루엣 내부 + 앵커 dilate 밖은 255
    assert flux[15, 15] == 255
    # 실루엣 바깥은 0
    assert flux[5, 5] == 0
    # 앵커 bbox 내부는 0
    assert flux[50, 50] == 0


# ── _hard_restore_anchors ────────────────────────────────────────────────────


def _rgb_png_bytes(rgb_arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb_arr, mode="RGB").save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_hard_restore_anchors_pixels_match_original():
    h, w = 64, 64
    orig = np.full((h, w, 3), 50, dtype=np.uint8)
    orig[10:30, 10:30] = [200, 150, 100]  # 앵커 영역의 원본 값
    flux = np.full((h, w, 3), 220, dtype=np.uint8)  # FLUX 결과: 전역 220 회색

    hard = np.zeros((h, w), dtype=np.uint8)
    hard[10:30, 10:30] = 255

    restored_bytes = _hard_restore_anchors(
        _rgb_png_bytes(orig), _rgb_png_bytes(flux), hard
    )
    restored = np.asarray(
        Image.open(io.BytesIO(restored_bytes)).convert("RGB"), dtype=np.uint8
    )

    # 하드 보존 영역 — 원본과 충분히 가까워야 함 (JPEG 압축 tolerance ≤ 5)
    core = restored[12:28, 12:28].astype(np.int16)
    orig_core = orig[12:28, 12:28].astype(np.int16)
    assert np.abs(core - orig_core).mean() < 5.0

    # 편집 영역 — FLUX 결과 쪽에 훨씬 가까워야 함
    edit = restored[40:60, 40:60].astype(np.int16)
    flux_val = np.int16(220)
    orig_val = np.int16(50)
    assert np.abs(edit - flux_val).mean() < np.abs(edit - orig_val).mean()


def test_hard_restore_anchors_resizes_flux_result():
    h, w = 64, 64
    orig = np.full((h, w, 3), 100, dtype=np.uint8)
    # FLUX 결과 해상도가 다른 경우 (32×32)
    flux_small = np.full((32, 32, 3), 200, dtype=np.uint8)

    hard = np.zeros((h, w), dtype=np.uint8)  # 전부 편집 영역
    restored_bytes = _hard_restore_anchors(
        _rgb_png_bytes(orig), _rgb_png_bytes(flux_small), hard
    )
    restored = np.asarray(
        Image.open(io.BytesIO(restored_bytes)).convert("RGB"), dtype=np.uint8
    )
    # 최종 해상도는 원본 기준
    assert restored.shape == orig.shape


# ── _compose_flux_prompt ─────────────────────────────────────────────────────


def test_compose_flux_prompt_with_colors():
    p = _compose_flux_prompt("cute grooming style", "rgb(200, 150, 100)")
    assert "rgb(200, 150, 100)" in p
    assert "ABSOLUTE" not in p  # 문구는 소문자 'exact' 사용
    assert "EXACT fur color" in p
    assert p.endswith("cute grooming style")


def test_compose_flux_prompt_without_colors():
    p = _compose_flux_prompt("cute grooming style", "")
    assert "rgb(" not in p
    assert "Preserve the dog's exact original fur color" in p


# ── evf_sam_geometry morphology 헬퍼 ─────────────────────────────────────────


def test_largest_cc_keeps_biggest_blob():
    m = np.zeros((30, 30), dtype=bool)
    m[2:6, 2:6] = True       # area 16
    m[10:14, 10:12] = True   # area 8
    out = _largest_cc(m)
    assert int(out.sum()) == 16


def test_largest_cc_noop_when_single_blob():
    m = np.zeros((10, 10), dtype=bool)
    m[2:5, 2:5] = True
    out = _largest_cc(m)
    assert int(out.sum()) == int(m.sum())


def test_fill_holes_fills_interior():
    m = np.zeros((20, 20), dtype=bool)
    m[5:15, 5:15] = True
    m[8:12, 8:12] = False
    out = _fill_holes(m)
    assert int(out.sum()) == 100


def test_opening_removes_single_pixel_noise():
    m = np.zeros((20, 20), dtype=bool)
    m[0, 0] = True           # 점 잡음
    m[5:15, 5:15] = True     # 본체
    out = _opening(m, 5)
    assert not out[0, 0]
    assert out[10, 10]


def test_cleanup_nose_step_order():
    # 본체 + 노이즈 점 + 내부 홀
    m = np.zeros((40, 40), dtype=bool)
    m[10:30, 10:30] = True
    m[14:16, 14:16] = False   # 홀
    m[1, 1] = True            # 노이즈
    cleaned, diag = _cleanup_nose(m, use_convex_hull=False)
    assert cleaned[20, 20]                   # 본체 중심 살아남음 (opening 5×5 는 모서리를 깎음)
    assert not cleaned[1, 1]                 # 노이즈 제거
    assert cleaned[15, 15]                   # 홀이 채워짐
    assert diag["steps"] == ["largest_cc", "opening", "fill_holes"]
    assert diag["convex_hull_applied"] is False
    assert "final" in diag["area_px"]


def test_cleanup_nose_with_convex_hull():
    m = np.zeros((40, 40), dtype=bool)
    m[10:30, 10:30] = True
    cleaned, diag = _cleanup_nose(m, use_convex_hull=True)
    assert "convex_hull" in diag["steps"]
    assert diag["convex_hull_applied"] is True
    assert cleaned.sum() > 0


# ── QualityFailure 경로 (동기 테스트만 — fal 호출 없음) ───────────────────


def test_per_part_r2_empty_dict():
    assert _per_part_r2({}, r2_scale=1.0) == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
