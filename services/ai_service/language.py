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
    "додай",
    "додати",
    "видали",
    "видалити",
    "архівуй",
    "архівувати",
    "зміни",
    "змінити",
    "онови",
    "оновити",
    "покажи",
    "показати",
    "скільки",
    "які",
    "яка",
    "який",
    "куплено",
    "купив",
    "купила",
    # RU
    "добавь",
    "добавить",
    "удали",
    "удалить",
    "архивируй",
    "архивировать",
    "измени",
    "изменить",
    "обнови",
    "обновить",
    "покажи",
    "показать",
    "сколько",
    "какие",
    "какая",
    "какой",
    "куплено",
    "купил",
    "купила",
    # EN
    "add",
    "delete",
    "remove",
    "archive",
    "change",
    "update",
    "show",
    "list",
    "how many",
    "how much",
    "bought",
)


def _looks_like_action_request(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(kw in lowered for kw in _ACTION_KEYWORDS)
