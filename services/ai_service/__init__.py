"""
AI service package: talks to NVIDIA NIM / Ollama, runs the tool-calling agent
loop, and formats responses for Telegram.

Split into submodules by concern:
    formatting.py - Markdown/HTML conversion helpers
    language.py   - language detection + action-intent heuristics
    prompts.py    - system prompt construction
    client.py     - raw HTTP calls to NVIDIA/Ollama + simple chat fallback
    agent.py      - the tool-calling agent loop and metric logging
"""

from .agent import (
    _dedupe_tool_calls,
    _extract_known_names,
    _find_ungrounded_names,
    _log_metric,
    _run_agent_loop,
    get_ai_agent_response,
)
from .client import _post_json, ask_nvidia, ask_nvidia_raw, ask_ollama, get_ai_response
from .formatting import format_markdown_to_html, strip_html_tags
from .language import _looks_like_action_request, _resolve_language, detect_message_language
from .prompts import system_prompt

__all__ = [
    "get_ai_agent_response",
    "get_ai_response",
    "ask_nvidia",
    "ask_nvidia_raw",
    "ask_ollama",
    "_post_json",
    "format_markdown_to_html",
    "strip_html_tags",
    "detect_message_language",
    "system_prompt",
    "_dedupe_tool_calls",
    "_extract_known_names",
    "_find_ungrounded_names",
    "_log_metric",
    "_run_agent_loop",
    "_looks_like_action_request",
    "_resolve_language",
]
