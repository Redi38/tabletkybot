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


def system_prompt(language: str = "ua") -> str:
    """Генерує системний промпт (англійською, мовно-нейтральний)."""
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
    return (
        "You are a helpful medical assistant in a Telegram bot for medication "
        "management. You help users track their medicines, dosages, schedules, "
        "and prescriptions. If given an image or PDF with test results, analyze "
        "it carefully. Always remind users to consult a doctor for serious "
        "medical concerns. "
        "CRITICAL LANGUAGE RULE: Always respond in the SAME language the user's "
        "LATEST message is written in, regardless of any other language used "
        "earlier in the conversation. If they write in Russian, respond in "
        "Russian. If Ukrainian, respond in Ukrainian. If English, respond in "
        "English. This rule overrides any other language preference. "
        "You have access to tools that fetch the user's REAL medicine and "
        "prescription data (get_my_medicines, get_my_prescriptions). When asked "
        "about the user's own medicines, doses, schedule, or prescriptions, you "
        "MUST call the appropriate tool — NEVER ask the user to provide this "
        "information themselves, since you already have direct access to it. "
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
    (включно з tool_calls, якщо модель вирішила викликати tool),
    а не тільки текст content.
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
) -> tuple[str, str]:
    """
    Агентний цикл: LLM може викликати tools (get_my_medicines,
    get_my_prescriptions) перед тим, як дати фінальну відповідь.
    """
    if not config.nvidia_api_key:
        return await get_ai_response(config, messages, language)

    conversation = list(messages)

    try:
        for _ in range(_MAX_AGENT_ITERATIONS):
            assistant_message = await ask_nvidia_raw(
                config.nvidia_api_key, config.nvidia_base_url,
                config.nvidia_model, conversation, tools=TOOL_SCHEMAS, language=language,
            )

            tool_calls = assistant_message.get("tool_calls")

            if not tool_calls:
                final_text = assistant_message.get("content") or ""
                return format_markdown_to_html(final_text), f"NVIDIA Agent ({config.nvidia_model})"

            conversation.append({
                "role": "assistant",
                "content": assistant_message.get("content"),
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                tool_name = call["function"]["name"]
                result = await execute_tool(tool_name, session, user_id)

                conversation.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

        logger.error(f"Агент не дав фінальну відповідь за {_MAX_AGENT_ITERATIONS} ітерацій")
        return get_text(language, "ai_err_api"), "none"

    except Exception as e:
        logger.error(f"Помилка агентного циклу NVIDIA: {type(e).__name__}: {e}")
        return await get_ai_response(config, messages, language)
