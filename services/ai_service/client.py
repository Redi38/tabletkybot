import logging

import aiohttp

from config import Config
from locales.texts import DEFAULT_LANG, get_text

from .formatting import format_markdown_to_html
from .language import _resolve_language
from .prompts import system_prompt

logger = logging.getLogger(__name__)

_NVIDIA_TIMEOUT = aiohttp.ClientTimeout(total=120)
_OLLAMA_TIMEOUT = aiohttp.ClientTimeout(total=120)
_AGENT_CALL_TIMEOUT = aiohttp.ClientTimeout(total=25)


def _nvidia_headers(api_key: str) -> dict:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}


async def _post_json(url: str, payload: dict, headers: dict | None, timeout: aiohttp.ClientTimeout) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload, timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.json()


async def ask_nvidia(
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict],
    language: str = DEFAULT_LANG,
) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt(language)}] + messages,
        "temperature": 0.7,
        "top_p": 0.95,
        "max_tokens": 800,
        "stream": False,
    }
    data = await _post_json(
        f"{base_url.rstrip('/')}/chat/completions",
        payload,
        _nvidia_headers(api_key),
        _NVIDIA_TIMEOUT,
    )
    return data["choices"][0]["message"]["content"]


async def ask_ollama(
    ollama_url: str,
    model: str,
    messages: list[dict],
    language: str = DEFAULT_LANG,
) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt(language)}] + messages,
        "stream": False,
        "options": {"temperature": 0.7},
    }
    data = await _post_json(f"{ollama_url}/api/chat", payload, None, _OLLAMA_TIMEOUT)
    if "message" in data:
        return data["message"]["content"]
    raise ValueError(f"Unexpected Ollama response: {data}")


async def ask_nvidia_raw(
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    language: str = DEFAULT_LANG,
    tool_choice: str = "auto",
) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt(language)}] + messages,
        "temperature": 0.7,
        "top_p": 0.95,
        "max_tokens": 800,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice

    data = await _post_json(
        f"{base_url.rstrip('/')}/chat/completions",
        payload,
        _nvidia_headers(api_key),
        _AGENT_CALL_TIMEOUT,
    )
    return data["choices"][0]["message"]


async def get_ai_response(config: Config, messages: list[dict], language: str = DEFAULT_LANG) -> tuple[str, str]:
    """Text request: NVIDIA → Ollama fallback."""
    language = _resolve_language(messages, language)

    if config.nvidia_api_key:
        try:
            response = await ask_nvidia(
                config.nvidia_api_key,
                config.nvidia_base_url,
                config.nvidia_model,
                messages,
                language,
            )
            return format_markdown_to_html(response), f"NVIDIA ({config.nvidia_model})"
        except Exception as e:
            logger.warning(f"NVIDIA API unavailable, falling back to Ollama: {type(e).__name__}: {e}")

    try:
        response = await ask_ollama(config.ollama_url, config.ollama_model, messages, language)
        return format_markdown_to_html(response), "Ollama (local)"
    except Exception as e:
        logger.error(f"Ollama fallback also failed, returning error to user: {type(e).__name__}: {e}")
        return get_text(language, "ai_err_api"), "none"
