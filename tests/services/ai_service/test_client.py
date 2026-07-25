"""
Tests for services/ai_service/client.py — raw HTTP calls to NVIDIA/Ollama
and the simple (non-agentic) chat fallback.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai_service import ask_nvidia, ask_nvidia_raw, ask_ollama, get_ai_response

from ._fixtures import _fake_config


class TestAskNvidia:
    async def test_returns_message_content(self):
        response = {"choices": [{"message": {"content": "Hello there"}}]}
        with patch("services.ai_service.client._post_json", AsyncMock(return_value=response)) as mock_post:
            result = await ask_nvidia(
                "key", "https://nim.example.com/v1", "model-x", [{"role": "user", "content": "hi"}]
            )

        assert result == "Hello there"
        url, payload, headers, timeout = mock_post.call_args.args
        assert url == "https://nim.example.com/v1/chat/completions"
        assert payload["model"] == "model-x"
        assert payload["messages"][0]["role"] == "system"
        assert headers["Authorization"] == "Bearer key"

    async def test_strips_trailing_slash_from_base_url(self):
        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch("services.ai_service.client._post_json", AsyncMock(return_value=response)) as mock_post:
            await ask_nvidia("key", "https://nim.example.com/v1/", "model-x", [])

        url = mock_post.call_args.args[0]
        assert url == "https://nim.example.com/v1/chat/completions"


class TestAskOllama:
    async def test_returns_message_content(self):
        response = {"message": {"content": "local reply"}}
        with patch("services.ai_service.client._post_json", AsyncMock(return_value=response)):
            result = await ask_ollama("http://localhost:11434", "llama3", [{"role": "user", "content": "hi"}])

        assert result == "local reply"

    async def test_raises_on_unexpected_response_shape(self):
        response = {"unexpected": "shape"}
        with patch("services.ai_service.client._post_json", AsyncMock(return_value=response)):
            with pytest.raises(ValueError):
                await ask_ollama("http://localhost:11434", "llama3", [])


class TestAskNvidiaRaw:
    async def test_returns_the_assistant_message(self):
        response = {"choices": [{"message": {"role": "assistant", "content": "hi", "tool_calls": None}}]}
        with patch("services.ai_service.client._post_json", AsyncMock(return_value=response)):
            result = await ask_nvidia_raw("key", "https://nim.example.com/v1", "model-x", [])

        assert result == {"role": "assistant", "content": "hi", "tool_calls": None}

    async def test_includes_tools_and_tool_choice_when_tools_given(self):
        response = {"choices": [{"message": {"content": "hi"}}]}
        with patch("services.ai_service.client._post_json", AsyncMock(return_value=response)) as mock_post:
            await ask_nvidia_raw(
                "key", "https://nim.example.com/v1", "model-x", [], tools=[{"type": "function"}], tool_choice="required"
            )

        payload = mock_post.call_args.args[1]
        assert payload["tools"] == [{"type": "function"}]
        assert payload["tool_choice"] == "required"

    async def test_omits_tools_key_when_no_tools_given(self):
        response = {"choices": [{"message": {"content": "hi"}}]}
        with patch("services.ai_service.client._post_json", AsyncMock(return_value=response)) as mock_post:
            await ask_nvidia_raw("key", "https://nim.example.com/v1", "model-x", [])

        payload = mock_post.call_args.args[1]
        assert "tools" not in payload
        assert "tool_choice" not in payload


class TestGetAiResponse:
    async def test_uses_nvidia_when_available(self):
        config = _fake_config()
        with patch("services.ai_service.client.ask_nvidia", AsyncMock(return_value="**Hi**")):
            text, model = await get_ai_response(config, [{"role": "user", "content": "hi"}])

        assert text == "<b>Hi</b>"
        assert "NVIDIA" in model

    async def test_falls_back_to_ollama_when_nvidia_fails(self):
        config = _fake_config()
        with (
            patch("services.ai_service.client.ask_nvidia", AsyncMock(side_effect=RuntimeError("down"))),
            patch("services.ai_service.client.ask_ollama", AsyncMock(return_value="local reply")),
        ):
            text, model = await get_ai_response(config, [{"role": "user", "content": "hi"}])

        assert text == "local reply"
        assert model == "Ollama (local)"

    async def test_uses_ollama_directly_when_no_nvidia_key(self):
        config = _fake_config(nvidia_api_key=None)
        with (
            patch("services.ai_service.client.ask_nvidia", AsyncMock()) as mock_nvidia,
            patch("services.ai_service.client.ask_ollama", AsyncMock(return_value="local reply")),
        ):
            text, model = await get_ai_response(config, [{"role": "user", "content": "hi"}])

        mock_nvidia.assert_not_awaited()
        assert text == "local reply"

    async def test_returns_error_text_when_both_fail(self):
        config = _fake_config()
        with (
            patch("services.ai_service.client.ask_nvidia", AsyncMock(side_effect=RuntimeError("down"))),
            patch("services.ai_service.client.ask_ollama", AsyncMock(side_effect=RuntimeError("also down"))),
        ):
            text, model = await get_ai_response(config, [{"role": "user", "content": "hi"}], language="en")

        assert model == "none"
        assert text  # some localized error string


class TestPostJson:
    async def test_posts_and_returns_json_on_success(self):
        from services.ai_service import _post_json

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={"ok": True})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            import aiohttp

            result = await _post_json(
                "https://example.com/api",
                {"key": "value"},
                {"Authorization": "Bearer x"},
                aiohttp.ClientTimeout(total=5),
            )

        assert result == {"ok": True}
        mock_resp.raise_for_status.assert_called_once()

    async def test_propagates_http_error(self):
        from services.ai_service import _post_json

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(side_effect=RuntimeError("HTTP 500"))
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            import aiohttp

            with pytest.raises(RuntimeError):
                await _post_json("https://example.com/api", {}, None, aiohttp.ClientTimeout(total=5))
