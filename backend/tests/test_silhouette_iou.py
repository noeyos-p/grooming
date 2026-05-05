"""services/silhouette_iou.py 단위 테스트.

rembg 실 호출은 수동 probe(run_silhouette_probe.py)에서 검증한다. 여기서는
IoU 수학·정렬·crop 경계만 검증.
"""

import numpy as np
import pytest

from services.silhouette_iou import (
    align_shape,
    crop_by_bbox,
    mask_to_png_bytes,
    silhouette_iou,
)


def test_iou_identical_is_one():
    m = np.zeros((100, 100), dtype=bool)
    m[20:80, 20:80] = True
    assert silhouette_iou(m, m) == 1.0


def test_iou_disjoint_is_zero():
    a = np.zeros((100, 100), dtype=bool)
    b = np.zeros((100, 100), dtype=bool)
    a[10:30, 10:30] = True
    b[60:90, 60:90] = True
    assert silhouette_iou(a, b) == 0.0


def test_iou_half_overlap():
    """40x40 사각형 2개, 절반 겹침 → IoU = 0.5 * 40*40 / (1.5 * 40*40) = 1/3."""
    a = np.zeros((100, 100), dtype=bool)
    b = np.zeros((100, 100), dtype=bool)
    a[20:60, 20:60] = True
    b[40:80, 20:60] = True
    iou = silhouette_iou(a, b)
    assert abs(iou - (1 / 3)) < 1e-6


def test_iou_empty_masks():
    a = np.zeros((50, 50), dtype=bool)
    b = np.zeros((50, 50), dtype=bool)
    # union=0 → 0.0 반환 (분모 0 예외 없이)
    assert silhouette_iou(a, b) == 0.0


def test_align_same_shape_noop():
    a = np.ones((80, 60), dtype=bool)
    b = np.ones((80, 60), dtype=bool)
    a2, b2 = align_shape(a, b)
    assert a2.shape == (80, 60)
    assert b2.shape == (80, 60)


def test_align_different_shape_resizes_to_orig():
    orig = np.ones((100, 100), dtype=bool)
    res = np.ones((50, 50), dtype=bool)
    o, r = align_shape(orig, res)
    assert o.shape == (100, 100)
    assert r.shape == (100, 100)
    assert r.all()  # NEAREST 업샘플 — 전부 True


def test_align_nearest_preserves_boolean_edge():
    """NEAREST 리사이즈는 경계를 계단으로 유지한다 (bilinear blur 금지 회귀 방지)."""
    res = np.zeros((4, 4), dtype=bool)
    res[0:2, 0:2] = True
    orig = np.zeros((8, 8), dtype=bool)
    _, r = align_shape(orig, res)
    # 2x 업샘플 NEAREST → 왼쪽 상단 4x4 블럭이 True
    assert r[0:4, 0:4].all()
    assert not r[4:, :].any()
    assert not r[:, 4:].any()


def test_iou_with_alignment():
    """동일 의미의 마스크지만 해상도가 다를 때 정렬 후 IoU≈1."""
    orig = np.zeros((200, 200), dtype=bool)
    orig[50:150, 50:150] = True
    res = np.zeros((100, 100), dtype=bool)
    res[25:75, 25:75] = True  # 동일 비율 동일 위치
    iou = silhouette_iou(orig, res)
    assert iou > 0.95


def test_crop_by_bbox_basic():
    m = np.zeros((100, 200), dtype=bool)
    m[10:50, 30:80] = True
    cropped = crop_by_bbox(m, {"xmin": 30, "ymin": 10, "xmax": 80, "ymax": 50})
    assert cropped.shape == (40, 50)
    assert cropped.all()


def test_crop_by_bbox_clamps_to_image():
    """bbox가 이미지 밖으로 나가도 clamp해서 유효 범위만 잘라냄."""
    m = np.ones((100, 100), dtype=bool)
    cropped = crop_by_bbox(m, {"xmin": -10, "ymin": -20, "xmax": 150, "ymax": 200})
    assert cropped.shape == (100, 100)


def test_crop_by_bbox_inverted_returns_empty():
    """xmax<xmin 같은 이상 bbox는 빈 array."""
    m = np.ones((100, 100), dtype=bool)
    cropped = crop_by_bbox(m, {"xmin": 80, "ymin": 80, "xmax": 50, "ymax": 50})
    assert cropped.size == 0


def test_mask_to_png_roundtrip():
    from PIL import Image
    import io

    m = np.zeros((10, 10), dtype=bool)
    m[2:8, 2:8] = True
    png = mask_to_png_bytes(m)
    loaded = np.asarray(Image.open(io.BytesIO(png)).convert("L")) >= 128
    assert np.array_equal(m, loaded)
