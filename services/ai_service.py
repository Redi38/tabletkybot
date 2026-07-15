import asyncio
import base64
import json
import logging
import re
import time

import aiohttp

from config import Config
from database import crud
from locales.texts import get_text
from services.ai_tools import TOOL_SCHEMAS, execute_tool

logger = logging.getLogger(__name__)

_NVIDIA_TIMEOUT = aiohttp.ClientTimeout(total=120)
_OLLAMA_TIMEOUT = aiohttp.ClientTimeout(total=120)
_VISION_TIMEOUT = aiohttp.ClientTimeout(total=180)

_AGENT_CALL_TIMEOUT = aiohttp.ClientTimeout(total=25)
_AGENT_TOTAL_TIMEOUT_SECONDS = 45

_MD_BOLD = re.compile(r'\*\*(.*?)\*\*')
_MD_H3 = re.compile(r'^###\s+(.*?)$', re.MULTILINE)
_MD_H2 = re.compile(r'^##\s+(.*?)$', re.MULTILINE)
_MD_H1 = re.compile(r'^#\s+(.*?)$', re.MULTILINE)
_MD_LIST = re.compile(r'^\*\s+', re.MULTILINE)

_HTML_TAG = re.compile(r'<[^>]+>')
_BOLD_CONTENT = re.compile(r'<b>(.*?)</b>')


def format_markdown_to_html(text: str) -> str:
    """Converts Markdown from the AI into Telegram-compatible HTML."""
    if not text:
        return text
    text = _MD_BOLD.sub(r'<b>\1</b>', text)
    text = _MD_H3.sub(r'<b>\1</b>\n', text)
    text = _MD_H2.sub(r'<b>\1</b>\n', text)
    text = _MD_H1.sub(r'<b>\1</b>\n', text)
    text = _MD_LIST.sub('- ', text)
    return text


def strip_html_tags(text: str) -> str:
    """Removes HTML tags (<b>, <i>, <code>, etc.) from the text."""
    if not text:
        return text
    return _HTML_TAG.sub('', text)


# ─── Detecting the language of the user's latest message ───────────────────
_UA_ONLY_CHARS = set("іїєґІЇЄҐ")
_RU_ONLY_CHARS = set("ёъыэЁЪЫЭ")

_LANG_NAMES = {"ua": "Ukrainian", "ru": "Russian", "en": "English"}


def detect_message_language(text: str) -> str | None:
    if not text:
        return None

    has_ua = any(ch in _UA_ONLY_CHARS for ch in text)
    has_ru = any(ch in _RU_ONLY_CHARS for ch in text)

    if has_ua and not has_ru:
        return "ua"
    if has_ru and not has_ua:
        return "ru"
    if has_ua and has_ru:
        return None

    letters = [ch for ch in text if ch.isalpha()]
    if letters and all(ord(ch) < 128 for ch in letters):
        return "en"

    return None


def _resolve_language(messages: list[dict], fallback: str) -> str:
    """Determines the language from the last user message; falls back to the profile language if it can't."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            detected = detect_message_language(msg.get("content", "") or "")
            return detected or fallback
    return fallback


# ─── Keywords that explicitly indicate an intent to do something with data ────────
_ACTION_KEYWORDS = (
    # UA
    "додай", "додати", "видали", "видалити", "архівуй", "архівувати",
    "зміни", "змінити", "онови", "оновити", "покажи", "показати",
    "скільки", "які", "яка", "який", "куплено", "купив", "купила",
    # RU
    "добавь", "добавить", "удали", "удалить", "архивируй", "архивировать",
    "измени", "изменить", "обнови", "обновить", "покажи", "показать",
    "сколько", "какие", "какая", "какой", "куплено", "купил", "купила",
    # EN
    "add", "delete", "remove", "archive", "change", "update", "show",
    "list", "how many", "how much", "bought",
)


def _looks_like_action_request(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(kw in lowered for kw in _ACTION_KEYWORDS)


def system_prompt(language: str = "ua") -> str:
    html_instruction = (
        "You MUST format your response using ONLY Telegram-supported HTML tags: "
        "<b>bold</b> for headings/key terms, <i>italic</i>, and <code>code</code>. "
        "NEVER use Markdown formatting like asterisks (**) or hashes (#). "
        "CRITICAL STRUCTURE RULES: "
        "1. Break your response into short, highly readable paragraphs. "
        "2. ALWAYS use double line breaks (empty lines) between different sections. "
        "3. For lists, EVERY item MUST start on a new line with a dash (-). "
        "4. Highlight medicine names, prices, and main ideas using <b> tags. "
        "Make the text visually appealing and easy to scan."
    )

    lang_name = _LANG_NAMES.get(language, "the same language as the user's latest message")
    language_rule = (
        f"CRITICAL LANGUAGE RULE: The user's most recent message is written in "
        f"{lang_name}. You MUST write your ENTIRE response in {lang_name}, "
        f"regardless of what language earlier messages, tool results, or any "
        f"other data in this conversation are written in. Do not mix languages "
        f"within your response. This rule overrides any other language "
        f"preference or instruction."
    )

    tool_silence_rule = (
        "SILENT TOOL CALLS RULE: When you decide to call a tool, call it "
        "directly through the tool-calling mechanism. NEVER write in your "
        "visible text that you are about to call a function, are calling a "
        "function, or have called a function (e.g. do NOT write phrases like "
        "'I am calling function X' or 'Викликаю функцію X'). Announcing a tool "
        "call in text instead of actually invoking it is a critical error. Your "
        "visible text should either be empty (when you are only calling tools) "
        "or contain ONLY the final answer for the user, never commentary about "
        "your own tool usage."
    )

    factual_grounding_rule = (
        "FACTUAL GROUNDING RULE: When you answer questions about the user's "
        "medicines or prescriptions, every name, dose, quantity, or date you "
        "mention MUST come directly from the most recent tool result in this "
        "conversation. NEVER invent, guess, or reuse a medicine/prescription "
        "name from earlier in the conversation if it is not present in the "
        "latest tool result. If the tool result is empty, say so plainly "
        "instead of making something up."
    )

    return (
        "You are a personal agent inside a Telegram bot that manages the user's "
        "medicines and prescriptions. You can look up, add, and update medicine "
        "reminders and prescriptions on the user's behalf using the tools "
        "available to you. "
        f"{language_rule} "
        "TOOL USAGE RULE: You have NO memory and NO way to actually add, change, "
        "archive, or delete anything except by calling a tool. When the user asks "
        "about their own medicines, doses, schedule, or prescriptions, you MUST "
        "call the appropriate tool to get real data. When the user asks to ADD, "
        "UPDATE, or CHANGE a medicine or prescription, you MUST call the matching "
        "tool (add_medicine_reminder, update_medicine, add_prescription_entry, "
        "update_prescription, mark_prescription_bought). You are STRICTLY "
        "FORBIDDEN from claiming an action succeeded unless you actually called "
        "the tool and it returned success. Never write that something was added, "
        "updated, or done unless a tool call actually happened in this turn. "
        f"{tool_silence_rule} "
        f"{factual_grounding_rule} "
        "REMOVAL RULE: When the user wants to archive or delete a medicine or "
        "prescription, immediately call request_medicine_removal or "
        "request_prescription_removal — do NOT ask for confirmation yourself in "
        "text, the system will show the user buttons to confirm. "
        "PLAIN TEXT RULE: Medicine and prescription names that you pass as tool "
        "arguments (e.g. medicine_name) MUST be plain text only — never include "
        "HTML tags like <b> or <i> in tool arguments, even if such tags appear "
        "in earlier messages of this conversation. "
        f"{html_instruction}"
    )


def _nvidia_headers(api_key: str) -> dict:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}


async def _post_json(url: str, payload: dict, headers: dict | None, timeout: aiohttp.ClientTimeout) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload, timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.json()


async def ask_nvidia(
        api_key: str, base_url: str, model: str,
        messages: list[dict], language: str = "ua",
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
        payload, _nvidia_headers(api_key), _NVIDIA_TIMEOUT,
    )
    return data["choices"][0]["message"]["content"]


async def ask_nvidia_vision(
        api_key: str, base_url: str, model: str,
        image_bytes: bytes, user_text: str, language: str = "ua",
) -> str:
    image_b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt(language)},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": user_text},
                ],
            },
        ],
        "temperature": 0.4,
        "max_tokens": 1024,
        "stream": False,
    }
    data = await _post_json(
        f"{base_url.rstrip('/')}/chat/completions",
        payload, _nvidia_headers(api_key), _NVIDIA_TIMEOUT,
    )
    return data["choices"][0]["message"]["content"]


async def ask_ollama(
        ollama_url: str, model: str, messages: list[dict], language: str = "ua",
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


async def ask_ollama_vision(
        ollama_url: str, model: str, image_bytes: bytes, user_text: str, language: str = "ua",
) -> str:
    image_b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt(language)},
            {"role": "user", "content": user_text, "images": [image_b64]},
        ],
        "stream": False,
        "options": {"temperature": 0.4},
    }
    data = await _post_json(f"{ollama_url.rstrip('/')}/api/chat", payload, None, _VISION_TIMEOUT)
    if "message" in data:
        return data["message"]["content"]
    raise ValueError(f"Unexpected Ollama vision response: {data}")


async def get_ai_response(
        config: Config, messages: list[dict], language: str = "ua"
) -> tuple[str, str]:
    """Text request: NVIDIA → Ollama fallback."""
    language = _resolve_language(messages, language)

    if config.nvidia_api_key:
        try:
            response = await ask_nvidia(
                config.nvidia_api_key, config.nvidia_base_url,
                config.nvidia_model, messages, language,
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


async def get_ai_vision_response(
        config: Config, image_bytes: bytes, user_text: str, language: str = "ua"
) -> tuple[str, str]:
    """Vision request: NVIDIA Vision → Ollama Vision fallback."""
    language = detect_message_language(user_text) or language

    if config.nvidia_api_key:
        try:
            response = await ask_nvidia_vision(
                config.nvidia_api_key, config.nvidia_base_url,
                config.nvidia_vision_model, image_bytes, user_text, language,
            )
            return format_markdown_to_html(response), f"NVIDIA Vision ({config.nvidia_vision_model})"
        except Exception as e:
            logger.warning(f"NVIDIA Vision unavailable, falling back to Ollama Vision: {type(e).__name__}: {e}")

    try:
        response = await ask_ollama_vision(
            config.ollama_url, config.ollama_vision_model, image_bytes, user_text, language
        )
        return format_markdown_to_html(response), f"Ollama Vision ({config.ollama_vision_model})"
    except Exception as e:
        logger.error(f"Ollama Vision fallback also failed, returning error to user: {type(e).__name__}: {e}")

    return get_text(language, "ai_err_vision"), "none"


_MAX_AGENT_ITERATIONS = 5  # protection against an infinite loop of tool calls


async def ask_nvidia_raw(
        api_key: str, base_url: str, model: str,
        messages: list[dict], tools: list[dict] | None = None, language: str = "ua",
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
        payload, _nvidia_headers(api_key), _AGENT_CALL_TIMEOUT,
    )
    return data["choices"][0]["message"]


def _dedupe_tool_calls(tool_calls: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for call in tool_calls:
        name = call.get("function", {}).get("name", "")
        raw_args = call.get("function", {}).get("arguments") or "{}"
        try:
            normalized_args = json.dumps(json.loads(raw_args), sort_keys=True, ensure_ascii=False)
        except json.JSONDecodeError:
            normalized_args = raw_args
        key = f"{name}:{normalized_args}"
        if key in seen:
            logger.warning(f"[DEDUPE] Skipping duplicate tool_call: {name} {raw_args}")
            continue
        seen.add(key)
        deduped.append(call)
    return deduped


def _extract_known_names(tool_name: str, result: dict) -> set[str]:
    """
    Extracts the set of ACTUAL medicine/prescription names from a read-tool
    result — used to check whether the model's final answer is not
    "hallucinating" a name that wasn't in the DB data.
    """
    names: set[str] = set()
    if tool_name == "get_my_medicines":
        for m in result.get("medicines", []) or []:
            name = m.get("name")
            if name:
                names.add(str(name).strip().lower())
    elif tool_name == "get_my_prescriptions":
        for p in result.get("prescriptions", []) or []:
            name = p.get("medicine_name")
            if name:
                names.add(str(name).strip().lower())
    return names


def _find_ungrounded_names(final_text: str, known_names: set[str]) -> list[str]:
    if not known_names:
        return []
    mentioned = [m.strip().lower() for m in _BOLD_CONTENT.findall(final_text)]
    candidates = [m for m in mentioned if len(m) >= 3]
    return [m for m in candidates if not any(m in known or known in m for known in known_names)]

async def get_ai_agent_response(
        config, session, user_id: int, messages: list[dict], language: str = "ua",
) -> tuple[str, str, dict | None]:
    start_time = time.monotonic()
    language = _resolve_language(messages, language)

    if not config.nvidia_api_key:
        text, model = await get_ai_response(config, messages, language)
        latency_ms = int((time.monotonic() - start_time) * 1000)
        await _log_metric(
            session, user_id, model_used=model,
            tool_choice=None, tool_names=None,
            latency_ms=latency_ms, status="success",
        )
        return strip_html_tags(text), model, None

    try:
        text, model, confirmation, meta = await asyncio.wait_for(
            _run_agent_loop(config, session, user_id, messages, language),
            timeout=_AGENT_TOTAL_TIMEOUT_SECONDS,
        )
        latency_ms = int((time.monotonic() - start_time) * 1000)
        await _log_metric(
            session, user_id, model_used=model,
            tool_choice=meta["tool_choice"], tool_names=meta["tool_names"],
            latency_ms=latency_ms, status="success",
        )
        return text, model, confirmation
    except asyncio.TimeoutError:
        latency_ms = int((time.monotonic() - start_time) * 1000)
        logger.error(
            f"Agent loop exceeded the overall time limit "
            f"({_AGENT_TOTAL_TIMEOUT_SECONDS}s) for user_id={user_id}"
        )
        await _log_metric(
            session, user_id, model_used="none",
            tool_choice=None, tool_names=None,
            latency_ms=latency_ms, status="timeout",
            error_message=f"Exceeded {_AGENT_TOTAL_TIMEOUT_SECONDS}s limit",
        )
        return get_text(language, "ai_err_api"), "none", None


async def _log_metric(
        session, user_id: int, model_used: str,
        tool_choice: str | None, tool_names: list[str] | None,
        latency_ms: int, status: str, error_message: str | None = None,
) -> None:
    """
    Best-effort metric logging — a failure here (e.g. a stale session) must
    never break the actual AI response the user is waiting for.
    """
    try:
        await crud.log_ai_metric(
            session, user_id=user_id, model_used=model_used,
            tool_choice=tool_choice, tool_names=tool_names,
            latency_ms=latency_ms, status=status, error_message=error_message,
        )
    except Exception as e:
        logger.warning(f"Failed to log AI metric for user_id={user_id}: {e}")


async def _run_agent_loop(
        config, session, user_id: int, messages: list[dict], language: str,
) -> tuple[str, str, dict | None, dict]:
    conversation = list(messages)
    last_user_text = messages[-1].get("content", "") if messages else ""
    known_names: set[str] = set()
    retried_for_grounding = False
    called_tool_names: list[str] = []
    first_tool_choice: str | None = None

    try:
        for iteration in range(_MAX_AGENT_ITERATIONS):
            force_tool = (
                iteration == 0
                and not retried_for_grounding
                and _looks_like_action_request(last_user_text)
            )
            tool_choice = "required" if force_tool else "auto"
            if first_tool_choice is None:
                first_tool_choice = tool_choice

            assistant_message = await ask_nvidia_raw(
                config.nvidia_api_key, config.nvidia_base_url,
                config.nvidia_model, conversation, tools=TOOL_SCHEMAS,
                language=language, tool_choice=tool_choice,
            )

            logger.debug(f"Raw NIM response (user_id={user_id}, iteration={iteration}): {assistant_message}")

            tool_calls = assistant_message.get("tool_calls")

            if not tool_calls:
                final_text = (assistant_message.get("content") or "").strip()

                ungrounded = _find_ungrounded_names(final_text, known_names)
                if ungrounded and not retried_for_grounding:
                    logger.warning(
                        f"[GROUNDING] The model mentioned data not present in "
                        f"the tool result: {ungrounded}. Making one retry."
                    )
                    conversation.append({"role": "assistant", "content": final_text})
                    conversation.append({
                        "role": "user",
                        "content": (
                            "System note: your previous answer mentioned data "
                            "that does not match the actual tool results. "
                            "Please re-answer using ONLY the exact names and "
                            "values from the tool results above."
                        ),
                    })
                    retried_for_grounding = True
                    continue

                meta: dict[str, str | list[str] | None] = {"tool_choice": first_tool_choice, "tool_names": called_tool_names}
                return final_text, f"NVIDIA Agent ({config.nvidia_model})", None, meta

            tool_calls = _dedupe_tool_calls(tool_calls)

            conversation.append({
                "role": "assistant",
                "content": assistant_message.get("content"),
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                tool_name = call["function"]["name"]
                called_tool_names.append(tool_name)
                raw_arguments = call["function"].get("arguments") or "{}"
                try:
                    parsed_arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    parsed_arguments = {}

                logger.info(f"Agent calling tool '{tool_name}' for user_id={user_id} with args={parsed_arguments}")

                result = await execute_tool(tool_name, session, user_id, parsed_arguments)

                if tool_name in ("get_my_medicines", "get_my_prescriptions"):
                    known_names |= _extract_known_names(tool_name, result)

                if result.get("requires_confirmation"):
                    meta = {"tool_choice": first_tool_choice, "tool_names": called_tool_names}
                    return "", f"NVIDIA Agent ({config.nvidia_model})", {
                        "target_type": result["target_type"],
                        "target_id": result["target_id"],
                        "target_name": result["target_name"],
                    }, meta

                conversation.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

        logger.error(f"The agent did not produce a final answer within {_MAX_AGENT_ITERATIONS} iterations")
        meta = {"tool_choice": first_tool_choice, "tool_names": called_tool_names}
        return get_text(language, "ai_err_api"), "none", None, meta

    except Exception as e:
        logger.error(f"NVIDIA agent loop error: {type(e).__name__}: {e}")
        text, model = await get_ai_response(config, messages, language)
        meta = {"tool_choice": first_tool_choice, "tool_names": called_tool_names}
        return strip_html_tags(text), model, None, meta
