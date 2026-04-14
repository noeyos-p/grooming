import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx


@pytest.mark.asyncio
async def test_run_gemini_pipeline_success(mock_gemini_client, mock_cloudinary_upload, sample_image_url, sample_image_bytes):
    """정상 흐름: Gemini 호출 -> Cloudinary 업로드 -> URL 반환"""
    with patch("httpx.AsyncClient") as mock_http:
        mock_response = MagicMock()
        mock_response.content = sample_image_bytes
        mock_response.raise_for_status = MagicMock()
        mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
            get=AsyncMock(return_value=mock_response)
        ))
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        from services.gemini_pipeline import run_gemini_pipeline
        result = await run_gemini_pipeline(sample_image_url, "maltese", "teddy_cut")

        assert result == "https://res.cloudinary.com/test/image/upload/test.jpg"
        mock_cloudinary_upload.assert_called_once()


@pytest.mark.asyncio
async def test_run_gemini_pipeline_invalid_breed(sample_image_url):
    """잘못된 breed_id -> ValueError"""
    from services.gemini_pipeline import run_gemini_pipeline
    with pytest.raises(ValueError):
        await run_gemini_pipeline(sample_image_url, "nonexistent_breed", "teddy_cut")


@pytest.mark.asyncio
async def test_run_gemini_pipeline_invalid_style(sample_image_url):
    """잘못된 style_id -> ValueError"""
    from services.gemini_pipeline import run_gemini_pipeline
    with pytest.raises(ValueError):
        await run_gemini_pipeline(sample_image_url, "maltese", "nonexistent_style")


@pytest.mark.asyncio
async def test_run_gemini_pipeline_api_error(mock_cloudinary_upload, sample_image_url, sample_image_bytes):
    """Gemini API 오류 -> RuntimeError"""
    with patch("services.gemini_pipeline.genai.Client") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        mock_client.models.generate_content.side_effect = Exception("API Error")

        with patch("httpx.AsyncClient") as mock_http:
            mock_response = MagicMock()
            mock_response.content = sample_image_bytes
            mock_response.raise_for_status = MagicMock()
            mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=mock_response)
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

            from services.gemini_pipeline import run_gemini_pipeline
            with pytest.raises(RuntimeError):
                await run_gemini_pipeline(sample_image_url, "maltese", "teddy_cut")


@pytest.mark.asyncio
async def test_run_gemini_pipeline_no_image_in_response(sample_image_url, sample_image_bytes):
    """Gemini가 이미지를 반환하지 않음 -> RuntimeError"""
    with patch("services.gemini_pipeline.genai.Client") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client

        # inline_data 속성이 없는 part만 반환
        mock_part = MagicMock(spec=[])
        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]
        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        with patch("httpx.AsyncClient") as mock_http:
            mock_response_http = MagicMock()
            mock_response_http.content = sample_image_bytes
            mock_response_http.raise_for_status = MagicMock()
            mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=mock_response_http)
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

            from services.gemini_pipeline import run_gemini_pipeline
            with pytest.raises(RuntimeError, match="이미지를 반환하지 않았습니다"):
                await run_gemini_pipeline(sample_image_url, "maltese", "teddy_cut")


@pytest.mark.asyncio
async def test_run_gemini_pipeline_cloudinary_error(mock_gemini_client, sample_image_url, sample_image_bytes):
    """Cloudinary 업로드 실패 -> RuntimeError"""
    with patch("cloudinary.uploader.upload", side_effect=Exception("Cloudinary Error")):
        with patch("httpx.AsyncClient") as mock_http:
            mock_response = MagicMock()
            mock_response.content = sample_image_bytes
            mock_response.raise_for_status = MagicMock()
            mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=mock_response)
            ))
            mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

            from services.gemini_pipeline import run_gemini_pipeline
            with pytest.raises(RuntimeError):
                await run_gemini_pipeline(sample_image_url, "maltese", "teddy_cut")
