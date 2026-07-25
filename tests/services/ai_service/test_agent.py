"""
Tests for services/ai_service/agent.py — the tool-calling agent loop,
name-grounding checks, and metric logging.
(_run_agent_loop itself lives in test_agent_loop.py)
"""

from unittest.mock import AsyncMock, MagicMock, patch

from services.ai_service import (
    _dedupe_tool_calls,
    _extract_known_names,
    _find_ungrounded_names,
    _log_metric,
    get_ai_agent_response,
)

from ._fixtures import _fake_config


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
