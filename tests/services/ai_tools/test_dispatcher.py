"""
Tests for services/ai_tools/dispatcher.py — the execute_tool dispatcher
and TOOL_EXECUTORS registry.
"""

from unittest.mock import AsyncMock

from services.ai_tools import execute_tool


class TestExecuteTool:
    async def test_unknown_tool_returns_error(self):
        session = AsyncMock()
        result = await execute_tool("nonexistent_tool", session, user_id=1)
        assert "error" in result
        assert "Unknown tool" in result["error"]

    async def test_exception_in_executor_rolls_back_and_returns_error(self, monkeypatch):
        async def broken_executor(session, user_id, args):
            raise RuntimeError("boom")

        from services import ai_tools

        monkeypatch.setitem(ai_tools.TOOL_EXECUTORS, "get_my_medicines", broken_executor)

        session = AsyncMock()
        result = await execute_tool("get_my_medicines", session, user_id=1)

        session.rollback.assert_awaited_once()
        assert "error" in result
