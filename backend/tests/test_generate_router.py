"""
POST /api/generate 라우팅 테스트 — Anchor-Inpaint v1.0 분기 검증.

검증 흐름:
 - invalid breed/style → 422 (파이프라인 호출 없이 style_prompts.get_prompt 단계에서 차단)
 - anchor-inpaint 성공 → 200 + result_url
 - anchor-inpaint QualityFailure → 422 (Gemini 폴백 금지)
 - anchor-inpaint RuntimeError → Gemini 폴백 성공 시 200
 - anchor-inpaint RuntimeError + Gemini RuntimeError → 500
"""

from unittest.mock import AsyncMock, patch

from services.evf_sam_geometry import QualityFailure


def test_health_endpoint(test_client):
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generate_endpoint_missing_fields(test_client):
    response = test_client.post("/api/generate", json={})
    assert response.status_code == 422


def test_generate_endpoint_invalid_breed(test_client):
    response = test_client.post(
        "/api/generate",
        json={
            "image_url": "https://example.com/dog.jpg",
            "breed_id": "invalid_breed",
            "style_id": "teddy_cut",
        },
    )
    assert response.status_code == 422


def test_generate_endpoint_anchor_success(test_client):
    with patch(
        "routers.generate.run_anchor_inpaint_pipeline",
        new=AsyncMock(return_value="https://res.cloudinary.com/test/anchor.jpg"),
    ):
        response = test_client.post(
            "/api/generate",
            json={
                "image_url": "https://example.com/dog.jpg",
                "breed_id": "maltese",
                "style_id": "teddy_cut",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["result_url"] == "https://res.cloudinary.com/test/anchor.jpg"
    assert "processing_time" in data


def test_generate_endpoint_quality_failure_returns_422(test_client):
    """QualityFailure 는 Gemini 로 폴백하지 말고 422 로 종료되어야 한다."""
    with patch(
        "routers.generate.run_anchor_inpaint_pipeline",
        new=AsyncMock(side_effect=QualityFailure("nose mask foreground 0")),
    ), patch(
        "routers.generate.run_gemini_pipeline",
        new=AsyncMock(return_value="https://should.not.be.called"),
    ) as mock_gemini:
        response = test_client.post(
            "/api/generate",
            json={
                "image_url": "https://example.com/dog.jpg",
                "breed_id": "maltese",
                "style_id": "teddy_cut",
            },
        )
    assert response.status_code == 422
    mock_gemini.assert_not_called()


def test_generate_endpoint_runtime_error_falls_back_to_gemini(test_client):
    with patch(
        "routers.generate.run_anchor_inpaint_pipeline",
        new=AsyncMock(side_effect=RuntimeError("FLUX timeout")),
    ), patch(
        "routers.generate.run_gemini_pipeline",
        new=AsyncMock(return_value="https://res.cloudinary.com/test/gemini.jpg"),
    ) as mock_gemini:
        response = test_client.post(
            "/api/generate",
            json={
                "image_url": "https://example.com/dog.jpg",
                "breed_id": "maltese",
                "style_id": "teddy_cut",
            },
        )
    assert response.status_code == 200
    assert response.json()["result_url"] == "https://res.cloudinary.com/test/gemini.jpg"
    mock_gemini.assert_awaited_once()


def test_generate_endpoint_both_pipelines_fail_returns_500(test_client):
    with patch(
        "routers.generate.run_anchor_inpaint_pipeline",
        new=AsyncMock(side_effect=RuntimeError("FLUX timeout")),
    ), patch(
        "routers.generate.run_gemini_pipeline",
        new=AsyncMock(side_effect=RuntimeError("Gemini timeout")),
    ):
        response = test_client.post(
            "/api/generate",
            json={
                "image_url": "https://example.com/dog.jpg",
                "breed_id": "maltese",
                "style_id": "teddy_cut",
            },
        )
    assert response.status_code == 500
