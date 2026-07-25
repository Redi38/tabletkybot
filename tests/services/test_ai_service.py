"""
Tests for the pure/stateless helper functions in services/ai_service.py.
These don't touch NVIDIA/Ollama APIs or the DB — they're regex/logic-only
transformations, so no mocking is needed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import Config
from services.ai_service import (
    _dedupe_tool_calls,
    _extract_known_names,
    _find_ungrounded_names,
    _log_metric,
    _looks_like_action_request,
    _resolve_language,
    _run_agent_loop,
    ask_nvidia,
    ask_nvidia_raw,
    ask_ollama,
    detect_message_language,
    format_markdown_to_html,
    get_ai_agent_response,
    get_ai_response,
    strip_html_tags,
    system_prompt,
)


class TestFormatMarkdownToHtml:
    def test_bold_conversion(self):
        assert format_markdown_to_html("**hello**") == "<b>hello</b>"

    def test_h1_h2_h3_conversion(self):
        assert format_markdown_to_html("# Title") == "<b>Title</b>\n"
        assert format_markdown_to_html("## Subtitle") == "<b>Subtitle</b>\n"
        assert format_markdown_to_html("### Small") == "<b>Small</b>\n"

    def test_list_marker_conversion(self):
        result = format_markdown_to_html("* item one")
        assert result == "- item one"

    def test_empty_string_returns_as_is(self):
        assert format_markdown_to_html("") == ""

    def test_none_returns_as_is(self):
        assert format_markdown_to_html(None) is None

    def test_plain_text_unaffected(self):
        text = "Just plain text, no markdown here."
        assert format_markdown_to_html(text) == text


class TestStripHtmlTags:
    def test_removes_bold_tags(self):
        assert strip_html_tags("<b>bold</b> text") == "bold text"

    def test_removes_multiple_tag_types(self):
        assert strip_html_tags("<b>a</b><i>b</i><code>c</code>") == "abc"

    def test_empty_string_returns_as_is(self):
        assert strip_html_tags("") == ""

    def test_no_tags_unaffected(self):
        assert strip_html_tags("no tags here") == "no tags here"


class TestDetectMessageLanguage:
    def test_detects_ukrainian_by_unique_chars(self):
        assert detect_message_language("Привіт, як справи?") == "ua"

    def test_detects_russian_by_unique_chars(self):
        assert detect_message_language("Привет, ещё раз") == "ru"

    def test_ambiguous_mixed_chars_returns_none(self):
        # contains both an UA-only char (і) and an RU-only char (ё)
        text = "привіт ещё"
        assert detect_message_language(text) is None

    def test_detects_english(self):
        assert detect_message_language("Hello, how are you?") == "en"

    def test_empty_string_returns_none(self):
        assert detect_message_language("") is None

    def test_none_input_returns_none(self):
        assert detect_message_language(None) is None

    def test_cyrillic_without_unique_chars_returns_none(self):
        text = "привет мир"
        assert detect_message_language(text) is None


class TestResolveLanguage:
    def test_uses_last_user_message(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Привіт"},
            {"role": "user", "content": "Привіт, як справи?"},
        ]
        assert _resolve_language(messages, fallback="en") == "ua"

    def test_falls_back_when_no_user_message(self):
        messages = [{"role": "assistant", "content": "Привіт"}]
        assert _resolve_language(messages, fallback="ru") == "ru"

    def test_falls_back_on_ambiguous_language(self):
        messages = [{"role": "user", "content": "123 456"}]
        assert _resolve_language(messages, fallback="ua") == "ua"


class TestLooksLikeActionRequest:
    def test_detects_ukrainian_action_keyword(self):
        assert _looks_like_action_request("Додай ібупрофен") is True

    def test_detects_russian_action_keyword(self):
        assert _looks_like_action_request("Удали это лекарство") is True

    def test_detects_english_action_keyword(self):
        assert _looks_like_action_request("Please add a new medicine") is True

    def test_plain_question_without_keywords(self):
        assert _looks_like_action_request("Дякую, все зрозуміло") is False

    def test_empty_string_returns_false(self):
        assert _looks_like_action_request("") is False

    def test_case_insensitive(self):
        assert _looks_like_action_request("ДОДАЙ ліки") is True


class TestDedupeToolCalls:
    def test_removes_exact_duplicate_calls(self):
        calls = [
            {"id": "1", "function": {"name": "get_my_medicines", "arguments": "{}"}},
            {"id": "2", "function": {"name": "get_my_medicines", "arguments": "{}"}},
        ]
        result = _dedupe_tool_calls(calls)
        assert len(result) == 1
        assert result[0]["id"] == "1"

    def test_keeps_calls_with_different_arguments(self):
        calls = [
            {"id": "1", "function": {"name": "update_medicine", "arguments": '{"id": 1}'}},
            {"id": "2", "function": {"name": "update_medicine", "arguments": '{"id": 2}'}},
        ]
        result = _dedupe_tool_calls(calls)
        assert len(result) == 2

    def test_dedupes_regardless_of_key_order_in_json(self):
        # {"a": 1, "b": 2} and {"b": 2, "a": 1} are semantically identical
        calls = [
            {"id": "1", "function": {"name": "update_medicine", "arguments": '{"a": 1, "b": 2}'}},
            {"id": "2", "function": {"name": "update_medicine", "arguments": '{"b": 2, "a": 1}'}},
        ]
        result = _dedupe_tool_calls(calls)
        assert len(result) == 1

    def test_handles_malformed_json_arguments_gracefully(self):
        calls = [
            {"id": "1", "function": {"name": "some_tool", "arguments": "not-json"}},
            {"id": "2", "function": {"name": "some_tool", "arguments": "not-json"}},
        ]
        result = _dedupe_tool_calls(calls)
        # Falls back to comparing the raw string — still dedupes identical malformed args
        assert len(result) == 1

    def test_empty_list_returns_empty(self):
        assert _dedupe_tool_calls([]) == []


class TestExtractKnownNames:
    def test_extracts_medicine_names(self):
        result = {"medicines": [{"name": "Aspirin"}, {"name": "Ibuprofen"}]}
        names = _extract_known_names("get_my_medicines", result)
        assert names == {"aspirin", "ibuprofen"}

    def test_extracts_prescription_names(self):
        result = {"prescriptions": [{"medicine_name": "Amoxicillin"}]}
        names = _extract_known_names("get_my_prescriptions", result)
        assert names == {"amoxicillin"}

    def test_unknown_tool_returns_empty_set(self):
        result = {"medicines": [{"name": "Aspirin"}]}
        names = _extract_known_names("some_other_tool", result)
        assert names == set()

    def test_empty_result_returns_empty_set(self):
        assert _extract_known_names("get_my_medicines", {}) == set()

    def test_names_are_normalized_lowercase_and_stripped(self):
        result = {"medicines": [{"name": "  Aspirin  "}]}
        names = _extract_known_names("get_my_medicines", result)
        assert names == {"aspirin"}


class TestFindUngroundedNames:
    def test_flags_name_not_in_known_set(self):
        text = "You are taking <b>Paracetamol</b> daily."
        known = {"aspirin"}
        result = _find_ungrounded_names(text, known)
        assert "paracetamol" in result

    def test_does_not_flag_known_name(self):
        text = "You are taking <b>Aspirin</b> daily."
        known = {"aspirin"}
        result = _find_ungrounded_names(text, known)
        assert result == []

    def test_empty_known_names_skips_check(self):
        # If we have no known names to compare against (e.g. no read-tool was
        # called yet), we can't meaningfully flag anything as "ungrounded".
        text = "You are taking <b>Anything</b> daily."
        result = _find_ungrounded_names(text, known_names=set())
        assert result == []

    def test_partial_match_is_not_flagged(self):
        # "Aspirin 500mg" mentioned in bold should count as grounded if the
        # known name "aspirin" is a substring of it.
        text = "Take <b>Aspirin 500mg</b> now."
        known = {"aspirin"}
        result = _find_ungrounded_names(text, known)
        assert result == []

    def test_ignores_short_bold_fragments(self):
        # Bold fragments under 3 chars are skipped (likely not a medicine name)
        text = "<b>ok</b> <b>Fakename</b>"
        known = {"aspirin"}
        result = _find_ungrounded_names(text, known)
        assert "ok" not in result
        assert "fakename" in result


class TestSystemPrompt:
    def test_names_ukrainian_language(self):
        assert "Ukrainian" in system_prompt("ua")

    def test_names_russian_language(self):
        assert "Russian" in system_prompt("ru")

    def test_names_english_language(self):
        assert "English" in system_prompt("en")

    def test_unknown_language_falls_back_to_generic_wording(self):
        prompt = system_prompt("fr")
        assert "the same language as the user's latest message" in prompt


def _fake_config(nvidia_api_key="test-key"):
    return Config(
        bot_token="t",
        webhook_host="https://example.com",
        nvidia_api_key=nvidia_api_key,
        nvidia_base_url="https://nim.example.com/v1",
        nvidia_model="test-model",
        ollama_url="http://localhost:11434",
        ollama_model="llama3",
    )


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


class TestLogMetric:
    async def test_calls_crud_log_ai_metric_with_given_fields(self):
        session = MagicMock()
        with patch("services.ai_service.agent.crud.log_ai_metric", AsyncMock()) as mock_log:
            await _log_metric(
                session,
                user_id=1,
                model_used="NVIDIA (x)",
                tool_choice="auto",
                tool_names=["get_my_medicines"],
                latency_ms=120,
                status="success",
            )

        mock_log.assert_awaited_once_with(
            session,
            user_id=1,
            model_used="NVIDIA (x)",
            tool_choice="auto",
            tool_names=["get_my_medicines"],
            latency_ms=120,
            status="success",
            error_message=None,
        )

    async def test_swallows_exception_instead_of_propagating(self):
        session = MagicMock()
        with patch("services.ai_service.agent.crud.log_ai_metric", AsyncMock(side_effect=RuntimeError("db gone"))):
            await _log_metric(
                session,
                user_id=1,
                model_used="none",
                tool_choice=None,
                tool_names=None,
                latency_ms=0,
                status="timeout",
            )  # should not raise


class TestGetAiAgentResponse:
    async def test_uses_plain_text_path_when_no_nvidia_key(self):
        config = _fake_config(nvidia_api_key=None)
        session = MagicMock()

        with (
            patch("services.ai_service.agent.get_ai_response", AsyncMock(return_value=("<b>hi</b>", "Ollama (local)"))),
            patch("services.ai_service.agent._log_metric", AsyncMock()) as mock_log,
        ):
            text, model, confirmation = await get_ai_agent_response(
                config, session, 1, [{"role": "user", "content": "hi"}]
            )

        assert text == "hi"  # HTML tags stripped
        assert model == "Ollama (local)"
        assert confirmation is None
        assert mock_log.call_args.kwargs["tool_choice"] is None

    async def test_uses_agent_loop_when_nvidia_key_present(self):
        config = _fake_config()
        session = MagicMock()
        loop_result = ("Final answer", "NVIDIA Agent (test-model)", None, {"tool_choice": "auto", "tool_names": []})

        with (
            patch("services.ai_service.agent._run_agent_loop", AsyncMock(return_value=loop_result)),
            patch("services.ai_service.agent._log_metric", AsyncMock()) as mock_log,
        ):
            text, model, confirmation = await get_ai_agent_response(
                config, session, 1, [{"role": "user", "content": "hi"}]
            )

        assert text == "Final answer"
        assert model == "NVIDIA Agent (test-model)"
        assert confirmation is None
        assert mock_log.call_args.kwargs["status"] == "success"

    async def test_returns_error_and_logs_timeout_on_overall_timeout(self):
        config = _fake_config()
        session = MagicMock()

        async def _never_finishes(*args, **kwargs):
            import asyncio

            await asyncio.sleep(999)

        with (
            patch("services.ai_service.agent._run_agent_loop", _never_finishes),
            patch("services.ai_service.agent._AGENT_TOTAL_TIMEOUT_SECONDS", 0.01),
            patch("services.ai_service.agent._log_metric", AsyncMock()) as mock_log,
        ):
            text, model, confirmation = await get_ai_agent_response(
                config, session, 1, [{"role": "user", "content": "hi"}]
            )

        assert model == "none"
        assert confirmation is None
        assert mock_log.call_args.kwargs["status"] == "timeout"


class TestRunAgentLoop:
    async def test_returns_final_text_when_no_tool_calls(self):
        config = _fake_config()
        session = MagicMock()
        assistant_message = {"content": "Just an answer.", "tool_calls": None}

        with patch("services.ai_service.agent.ask_nvidia_raw", AsyncMock(return_value=assistant_message)):
            text, model, confirmation, meta = await _run_agent_loop(
                config, session, 1, [{"role": "user", "content": "hi"}], "en"
            )

        assert text == "Just an answer."
        assert confirmation is None
        assert "test-model" in model

    async def test_executes_a_tool_call_then_returns_final_answer(self):
        config = _fake_config()
        session = MagicMock()
        tool_call_message = {
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "get_my_medicines", "arguments": "{}"}}],
        }
        final_message = {"content": "Here are your medicines.", "tool_calls": None}

        with (
            patch(
                "services.ai_service.agent.ask_nvidia_raw", AsyncMock(side_effect=[tool_call_message, final_message])
            ),
            patch("services.ai_service.agent.execute_tool", AsyncMock(return_value={"medicines": []})) as mock_exec,
        ):
            text, model, confirmation, meta = await _run_agent_loop(
                config, session, 1, [{"role": "user", "content": "show my medicines"}], "en"
            )

        mock_exec.assert_awaited_once_with("get_my_medicines", session, 1, {})
        assert text == "Here are your medicines."
        assert meta["tool_names"] == ["get_my_medicines"]

    async def test_returns_confirmation_dict_when_tool_requires_confirmation(self):
        config = _fake_config()
        session = MagicMock()
        tool_call_message = {
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "request_medicine_removal", "arguments": "{}"}}],
        }
        confirm_result = {
            "requires_confirmation": True,
            "target_type": "medicine",
            "target_id": 5,
            "target_name": "Aspirin",
        }

        with (
            patch("services.ai_service.agent.ask_nvidia_raw", AsyncMock(return_value=tool_call_message)),
            patch("services.ai_service.agent.execute_tool", AsyncMock(return_value=confirm_result)),
        ):
            text, model, confirmation, meta = await _run_agent_loop(
                config, session, 1, [{"role": "user", "content": "delete aspirin"}], "en"
            )

        assert text == ""
        assert confirmation == {"target_type": "medicine", "target_id": 5, "target_name": "Aspirin"}

    async def test_falls_back_to_get_ai_response_on_exception(self):
        config = _fake_config()
        session = MagicMock()

        with (
            patch("services.ai_service.agent.ask_nvidia_raw", AsyncMock(side_effect=RuntimeError("NIM is down"))),
            patch(
                "services.ai_service.agent.get_ai_response",
                AsyncMock(return_value=("<b>fallback</b>", "Ollama (local)")),
            ),
        ):
            text, model, confirmation, meta = await _run_agent_loop(
                config, session, 1, [{"role": "user", "content": "hi"}], "en"
            )

        assert text == "fallback"  # HTML stripped
        assert model == "Ollama (local)"
        assert confirmation is None

    async def test_stops_after_max_iterations_without_final_answer(self):
        config = _fake_config()
        session = MagicMock()
        # Every call returns another tool call, never a final answer.
        looping_message = {
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "get_my_medicines", "arguments": "{}"}}],
        }

        with (
            patch("services.ai_service.agent.ask_nvidia_raw", AsyncMock(return_value=looping_message)),
            patch("services.ai_service.agent.execute_tool", AsyncMock(return_value={"medicines": []})),
        ):
            text, model, confirmation, meta = await _run_agent_loop(
                config, session, 1, [{"role": "user", "content": "show my medicines"}], "en"
            )

        assert model == "none"
        assert confirmation is None

    async def test_retries_once_on_ungrounded_name_then_returns_grounded_answer(self):
        config = _fake_config()
        session = MagicMock()
        tool_call_message = {
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "get_my_medicines", "arguments": "{}"}}],
        }
        ungrounded_answer = {"content": "You take <b>Paracetamol</b> daily.", "tool_calls": None}
        grounded_answer = {"content": "You take <b>Aspirin</b> daily.", "tool_calls": None}

        with (
            patch(
                "services.ai_service.agent.ask_nvidia_raw",
                AsyncMock(side_effect=[tool_call_message, ungrounded_answer, grounded_answer]),
            ),
            patch(
                "services.ai_service.agent.execute_tool",
                AsyncMock(return_value={"medicines": [{"name": "Aspirin"}]}),
            ),
        ):
            text, model, confirmation, meta = await _run_agent_loop(
                config, session, 1, [{"role": "user", "content": "what do I take"}], "en"
            )

        assert text == "You take <b>Aspirin</b> daily."

    async def test_deduplicates_identical_tool_calls_before_executing(self):
        config = _fake_config()
        session = MagicMock()
        duplicate_call_message = {
            "content": None,
            "tool_calls": [
                {"id": "call_1", "function": {"name": "get_my_medicines", "arguments": "{}"}},
                {"id": "call_2", "function": {"name": "get_my_medicines", "arguments": "{}"}},
            ],
        }
        final_message = {"content": "Done.", "tool_calls": None}

        with (
            patch(
                "services.ai_service.agent.ask_nvidia_raw",
                AsyncMock(side_effect=[duplicate_call_message, final_message]),
            ),
            patch("services.ai_service.agent.execute_tool", AsyncMock(return_value={"medicines": []})) as mock_exec,
        ):
            await _run_agent_loop(config, session, 1, [{"role": "user", "content": "show meds"}], "en")

        mock_exec.assert_awaited_once()  # the duplicate was deduped away

    async def test_forces_tool_choice_required_for_action_request_on_first_iteration(self):
        config = _fake_config()
        session = MagicMock()
        final_message = {"content": "Added.", "tool_calls": None}

        with patch("services.ai_service.agent.ask_nvidia_raw", AsyncMock(return_value=final_message)) as mock_ask:
            await _run_agent_loop(config, session, 1, [{"role": "user", "content": "додай ліки"}], "ua")

        assert mock_ask.call_args.kwargs["tool_choice"] == "required"

    async def test_uses_auto_tool_choice_for_non_action_request(self):
        config = _fake_config()
        session = MagicMock()
        final_message = {"content": "Just chatting.", "tool_calls": None}

        with patch("services.ai_service.agent.ask_nvidia_raw", AsyncMock(return_value=final_message)) as mock_ask:
            await _run_agent_loop(config, session, 1, [{"role": "user", "content": "hello there"}], "en")

        assert mock_ask.call_args.kwargs["tool_choice"] == "auto"

    async def test_falls_back_to_empty_arguments_on_malformed_json(self):
        config = _fake_config()
        session = MagicMock()
        tool_call_message = {
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "get_my_medicines", "arguments": "{not valid json"}}],
        }
        final_message = {"content": "Done.", "tool_calls": None}

        with (
            patch(
                "services.ai_service.agent.ask_nvidia_raw", AsyncMock(side_effect=[tool_call_message, final_message])
            ),
            patch("services.ai_service.agent.execute_tool", AsyncMock(return_value={"medicines": []})) as mock_exec,
        ):
            await _run_agent_loop(config, session, 1, [{"role": "user", "content": "show meds"}], "en")

        mock_exec.assert_awaited_once_with("get_my_medicines", session, 1, {})


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
