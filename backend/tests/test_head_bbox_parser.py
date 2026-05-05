"""_parse_head_bbox_payload 단위 테스트.

배치 진단(N=4)에서 Gemini 2.5 Flash가 프롬프트 스키마를 무시하고 native `box_2d`
포맷을 반환해 15/16 실패했던 이슈의 회귀 방지용.
"""

import pytest

from services.inpaint_pipeline import _parse_head_bbox_payload


IMG_W = 3024
IMG_H = 4032


def test_xyxy_schema_100_scale():
    """프롬프트 요청 스키마 — 0-100 퍼센트."""
    data = {"xmin": 10, "ymin": 20, "xmax": 90, "ymax": 80}
    bbox = _parse_head_bbox_payload(data, IMG_W, IMG_H)
    assert bbox == {
        "xmin": int(10 / 100 * IMG_W),
        "ymin": int(20 / 100 * IMG_H),
        "xmax": int(90 / 100 * IMG_W),
        "ymax": int(80 / 100 * IMG_H),
    }


def test_box_2d_1000_scale_anchor_maltese():
    """실제 Gemini 응답(anchor/maltese) — box_2d 0-1000 스케일."""
    data = {"box_2d": [17, 17, 650, 954], "label": "the dog's ENTIRE HEAD"}
    bbox = _parse_head_bbox_payload(data, IMG_W, IMG_H)
    assert bbox == {
        "ymin": int(17 / 1000 * IMG_H),
        "xmin": int(17 / 1000 * IMG_W),
        "ymax": int(650 / 1000 * IMG_H),
        "xmax": int(954 / 1000 * IMG_W),
    }


def test_box_2d_1000_scale_gemini_shih_tzu():
    """실제 Gemini 응답(gemini/shih_tzu) — box_2d 최댓값이 1000."""
    data = {"box_2d": [33, 85, 874, 1000], "label": "dog's ENTIRE HEAD"}
    bbox = _parse_head_bbox_payload(data, IMG_W, IMG_H)
    assert bbox["xmax"] == IMG_W  # 1000/1000 * W


def test_box_2d_100_scale_autodetect():
    """실제 Gemini 응답(gemini/maltese) — box_2d가 0-100 스케일로 오는 경우."""
    data = {"box_2d": [11, 15, 69.5, 81]}
    bbox = _parse_head_bbox_payload(data, IMG_W, IMG_H)
    assert bbox == {
        "ymin": int(11 / 100 * IMG_H),
        "xmin": int(15 / 100 * IMG_W),
        "ymax": int(69.5 / 100 * IMG_H),
        "xmax": int(81 / 100 * IMG_W),
    }


def test_box_2d_boundary_100():
    """경계값 — 최댓값이 정확히 100이면 0-100 스케일 판정."""
    data = {"box_2d": [0, 0, 100, 100]}
    bbox = _parse_head_bbox_payload(data, IMG_W, IMG_H)
    assert bbox == {"xmin": 0, "ymin": 0, "xmax": IMG_W, "ymax": IMG_H}


def test_box_2d_just_over_100_switches_to_1000():
    """경계값 — 최댓값이 101이면 0-1000 스케일 판정."""
    data = {"box_2d": [0, 0, 101, 101]}
    bbox = _parse_head_bbox_payload(data, IMG_W, IMG_H)
    assert bbox["xmax"] == int(101 / 1000 * IMG_W)


def test_invalid_schema_returns_none():
    """알 수 없는 키 조합 → None."""
    assert _parse_head_bbox_payload({"foo": "bar"}, IMG_W, IMG_H) is None


def test_box_2d_wrong_length_returns_none():
    """box_2d 배열 길이가 4가 아니면 None."""
    assert _parse_head_bbox_payload({"box_2d": [1, 2, 3]}, IMG_W, IMG_H) is None


def test_box_2d_non_list_returns_none():
    """box_2d가 리스트가 아니면 None."""
    assert _parse_head_bbox_payload({"box_2d": "1,2,3,4"}, IMG_W, IMG_H) is None


def test_inverted_bbox_returns_none():
    """xmax ≤ xmin 또는 ymax ≤ ymin → None (역전된 bbox 차단)."""
    assert _parse_head_bbox_payload(
        {"xmin": 90, "ymin": 20, "xmax": 10, "ymax": 80}, IMG_W, IMG_H
    ) is None
    assert _parse_head_bbox_payload(
        {"box_2d": [80, 10, 20, 90]}, IMG_W, IMG_H
    ) is None


def test_xyxy_precedence_over_box_2d():
    """둘 다 존재할 경우 xyxy 우선 (프롬프트 요청 형태 존중)."""
    data = {"xmin": 10, "ymin": 10, "xmax": 90, "ymax": 90, "box_2d": [0, 0, 1000, 1000]}
    bbox = _parse_head_bbox_payload(data, IMG_W, IMG_H)
    assert bbox["xmax"] == int(90 / 100 * IMG_W)
