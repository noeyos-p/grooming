import pytest
from unittest.mock import patch


def test_health_endpoint(test_client):
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generate_endpoint_missing_fields(test_client):
    response = test_client.post("/api/generate", json={})
    assert response.status_code == 422


def test_generate_endpoint_invalid_breed(test_client):
    with patch("routers.generate.run_pipeline", side_effect=ValueError("Invalid breed")):
        response = test_client.post("/api/generate", json={
            "image_url": "https://example.com/dog.jpg",
            "breed_id": "invalid_breed",
            "style_id": "teddy_cut"
        })
        assert response.status_code == 422


def test_generate_endpoint_success(test_client):
    with patch("routers.generate.run_pipeline", return_value="https://res.cloudinary.com/test/result.jpg"):
        response = test_client.post("/api/generate", json={
            "image_url": "https://example.com/dog.jpg",
            "breed_id": "maltese",
            "style_id": "teddy_cut"
        })
        assert response.status_code == 200
        data = response.json()
        assert "result_url" in data
        assert "processing_time" in data
        assert data["result_url"] == "https://res.cloudinary.com/test/result.jpg"


def test_generate_endpoint_pipeline_error(test_client):
    with patch("routers.generate.run_pipeline", side_effect=RuntimeError("Pipeline failed")):
        response = test_client.post("/api/generate", json={
            "image_url": "https://example.com/dog.jpg",
            "breed_id": "maltese",
            "style_id": "teddy_cut"
        })
        assert response.status_code == 500
