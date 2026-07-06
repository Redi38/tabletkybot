import aiohttp
import logging
import base64
import re
import json
from services.ai_tools import TOOL_SCHEMAS, execute_tool
from config import Config
from locales.texts import get_text

logger = logging.getLogger(__name__)

_NVIDIA_TIMEOUT = aiohttp.ClientTimeout(total=120)
_OLLAMA_TIMEOUT = aiohttp.ClientTimeout(total=120)
_VISION_TIMEOUT = aiohttp.ClientTimeout(total=180)

_MD_BOLD = re.compile(r'\*\*(.*?)\*\*')
_MD_H3 = re.compile(r'^###\s+(.*?)$', re.MULTILINE)
_MD_H2 = re.compile(r'^##\s+(.*?)$', re.MULTILINE)
_MD_H1 = re.compile(r'^#\s+(.*?)$', re.MULTILINE)
_MD_LIST = re.compile(r'^\*\s+', re.MULTILINE)


def format_markdown_to_html(text: str) -> str:
    """Конвертує Markdown від ШІ у Telegram-сумісний HTML."""
    if not text:
        return text
    text = _MD_BOLD.sub(r'<b>\1</b>', text)
    text = _MD_H3.sub(r'<b>\1</b>\n', text)
    text = _MD_H2.sub(r'<b>\1</b>\n', text)
    text = _MD_H1.sub(r'<b>\1</b>\n', text)
    text = _MD_LIST.sub('- ', text)
    return text


# ─── Визначення мови останнього повідомлення користувача ───────────────────
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
    """Визначає мову за останнім повідомленням user; якщо не вдалося — fallback (мова профілю)."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            detected = detect_message_language(msg.get("content", "") or "")
            return detected or fallback
    return fallback


def system_prompt(language: str = "ua") -> str:
    """Генерує системний промпт (англійською, з чіткою вказівкою поточної мови відповіді)."""
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
        "REMOVAL RULE: When the user wants to archive or delete a medicine or "
        "prescription, immediately call request_medicine_removal or "
        "request_prescription_removal — do NOT ask for confirmation yourself in "
        "text, the system will show the user buttons to confirm. "
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
    """Текстовий запит: NVIDIA → Ollama fallback."""
    language = _resolve_language(messages, language)

    if config.nvidia_api_key:
        try:
            response = await ask_nvidia(
                config.nvidia_api_key, config.nvidia_base_url,
                config.nvidia_model, messages, language,
            )
            return format_markdown_to_html(response), f"NVIDIA ({config.nvidia_model})"
        except Exception as e:
            logger.error(f"NVIDIA API помилка: {type(e).__name__}: {e}")

    try:
        response = await ask_ollama(config.ollama_url, config.ollama_model, messages, language)
        return format_markdown_to_html(response), "Ollama (локальна)"
    except Exception as e:
        logger.error(f"Ollama помилка: {type(e).__name__}: {e}")
        return get_text(language, "ai_err_api"), "none"


async def get_ai_vision_response(
        config: Config, image_bytes: bytes, user_text: str, language: str = "ua"
) -> tuple[str, str]:
    """Vision запит: NVIDIA Vision → Ollama Vision fallback."""
    language = detect_message_language(user_text) or language

    if config.nvidia_api_key:
        try:
            response = await ask_nvidia_vision(
                config.nvidia_api_key, config.nvidia_base_url,
                config.nvidia_vision_model, image_bytes, user_text, language,
            )
            return format_markdown_to_html(response), f"NVIDIA Vision ({config.nvidia_vision_model})"
        except Exception as e:
            logger.error(f"NVIDIA Vision помилка: {type(e).__name__}: {e}")

    try:
        response = await ask_ollama_vision(
            config.ollama_url, config.ollama_vision_model, image_bytes, user_text, language
        )
        return format_markdown_to_html(response), f"Ollama Vision ({config.ollama_vision_model})"
    except Exception as e:
        logger.error(f"Ollama Vision помилка: {type(e).__name__}: {e}")

    return get_text(language, "ai_err_vision"), "none"


_MAX_AGENT_ITERATIONS = 5  # захист від нескінченного циклу tool-викликів


async def ask_nvidia_raw(
        api_key: str, base_url: str, model: str,
        messages: list[dict], tools: list[dict] | None = None, language: str = "ua",
) -> dict:
    """
    Те саме що ask_nvidia, але повертає ПОВНЕ повідомлення асистента
    (включно з tool_calls, якщо модель вирішила викликати tool).
    """
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
        payload["tool_choice"] = "auto"

    data = await _post_json(
        f"{base_url.rstrip('/')}/chat/completions",
        payload, _nvidia_headers(api_key), _NVIDIA_TIMEOUT,
    )
    return data["choices"][0]["message"]


async def get_ai_agent_response(
        config, session, user_id: int, messages: list[dict], language: str = "ua",
) -> tuple[str, str, dict | None]:
    """
    Агентний цикл: LLM може викликати tools (читання і запис ліків/рецептів)
    перед тим, як дати фінальну відповідь. Мова визначається один раз на
    початку — за останнім повідомленням користувача.

    Повертає (текст, назва_моделі, confirmation).
    confirmation — None у звичайному випадку, або dict
    {"target_type": "medicine"/"prescription", "target_id": int, "target_name": str}
    якщо потрібно показати юзеру кнопки архівувати/видалити/скасувати.
    """
    language = _resolve_language(messages, language)

    if not config.nvidia_api_key:
        text, model = await get_ai_response(config, messages, language)
        return text, model, None

    conversation = list(messages)

    try:
        for _ in range(_MAX_AGENT_ITERATIONS):
            assistant_message = await ask_nvidia_raw(
                config.nvidia_api_key, config.nvidia_base_url,
                config.nvidia_model, conversation, tools=TOOL_SCHEMAS, language=language,
            )

            logger.info(f"[DEBUG] Сира відповідь NIM: {assistant_message}")

            tool_calls = assistant_message.get("tool_calls")

            if not tool_calls:
                final_text = assistant_message.get("content") or ""
                return format_markdown_to_html(final_text), f"NVIDIA Agent ({config.nvidia_model})", None

            conversation.append({
                "role": "assistant",
                "content": assistant_message.get("content"),
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                tool_name = call["function"]["name"]
                raw_arguments = call["function"].get("arguments") or "{}"
                try:
                    parsed_arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    parsed_arguments = {}

                result = await execute_tool(tool_name, session, user_id, parsed_arguments)

                if result.get("requires_confirmation"):
                    return "", f"NVIDIA Agent ({config.nvidia_model})", {
                        "target_type": result["target_type"],
                        "target_id": result["target_id"],
                        "target_name": result["target_name"],
                    }

                conversation.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

        logger.error(f"Агент не дав фінальну відповідь за {_MAX_AGENT_ITERATIONS} ітерацій")
        return get_text(language, "ai_err_api"), "none", None

    except Exception as e:
        logger.error(f"Помилка агентного циклу NVIDIA: {type(e).__name__}: {e}")
        text, model = await get_ai_response(config, messages, language)
        return text, model, None
