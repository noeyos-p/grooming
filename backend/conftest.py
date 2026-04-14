import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture
def sample_image_url():
    return "https://example.com/dog.jpg"


@pytest.fixture
def sample_image_bytes():
    # 1x1 픽셀 PNG (최소 유효 이미지)
    return b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'


@pytest.fixture
def mock_cloudinary_upload():
    with patch("cloudinary.uploader.upload") as mock:
        mock.return_value = {"secure_url": "https://res.cloudinary.com/test/image/upload/test.jpg"}
        yield mock


@pytest.fixture
def mock_gemini_client():
    with patch("services.gemini_pipeline.genai.Client") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client

        # 정상 응답 mock 구성
        mock_part = MagicMock()
        mock_part.inline_data = MagicMock()
        mock_part.inline_data.data = b"fake_image_bytes"

        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        mock_client.models.generate_content.return_value = mock_response
        yield mock_client


@pytest.fixture
def test_client():
    from main import app
    return TestClient(app)
