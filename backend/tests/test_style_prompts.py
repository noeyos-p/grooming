import pytest
from services.style_prompts import get_prompt, get_all_breeds, BREEDS


def test_get_prompt_valid():
    """유효한 breed+style 조합 -> dict 반환, prompt/negative_prompt 키 존재"""
    result = get_prompt("maltese", "teddy_cut")
    assert result is not None
    assert isinstance(result, dict)
    assert "prompt" in result
    assert "negative_prompt" in result
    assert len(result["prompt"]) > 0
    assert len(result["negative_prompt"]) > 0


def test_get_prompt_invalid_breed():
    """존재하지 않는 breed_id -> None 반환"""
    result = get_prompt("nonexistent_breed", "teddy_cut")
    assert result is None


def test_get_prompt_invalid_style():
    """존재하지 않는 style_id -> None 반환"""
    result = get_prompt("maltese", "nonexistent_style")
    assert result is None


def test_get_all_breeds():
    """전체 견종 목록 반환 — 11개"""
    breeds = get_all_breeds()
    assert isinstance(breeds, list)
    assert len(breeds) == 11

    # 각 항목이 필수 필드를 포함하는지 확인
    for breed in breeds:
        assert "id" in breed
        assert "name" in breed
        assert "styles" in breed
        assert isinstance(breed["styles"], list)


def test_prompt_has_required_fields():
    """모든 견종/스타일 조합에 대해 필수 필드 존재 확인"""
    for breed_id, breed_data in BREEDS.items():
        for style_id in breed_data["styles"]:
            result = get_prompt(breed_id, style_id)
            assert result is not None, f"get_prompt({breed_id!r}, {style_id!r}) returned None"
            assert "prompt" in result, f"'prompt' key missing for {breed_id}/{style_id}"
            assert "negative_prompt" in result, f"'negative_prompt' key missing for {breed_id}/{style_id}"
            assert isinstance(result["prompt"], str), f"prompt not str for {breed_id}/{style_id}"
            assert isinstance(result["negative_prompt"], str), f"negative_prompt not str for {breed_id}/{style_id}"
            assert len(result["prompt"]) > 0, f"empty prompt for {breed_id}/{style_id}"
