import re

_MD_BOLD = re.compile(r"\*\*(.*?)\*\*")
_MD_H3 = re.compile(r"^###\s+(.*?)$", re.MULTILINE)
_MD_H2 = re.compile(r"^##\s+(.*?)$", re.MULTILINE)
_MD_H1 = re.compile(r"^#\s+(.*?)$", re.MULTILINE)
_MD_LIST = re.compile(r"^\*\s+", re.MULTILINE)

_HTML_TAG = re.compile(r"<[^>]+>")


def format_markdown_to_html(text: str) -> str:
    """Converts Markdown from the AI into Telegram-compatible HTML."""
    if not text:
        return text
    text = _MD_BOLD.sub(r"<b>\1</b>", text)
    text = _MD_H3.sub(r"<b>\1</b>\n", text)
    text = _MD_H2.sub(r"<b>\1</b>\n", text)
    text = _MD_H1.sub(r"<b>\1</b>\n", text)
    text = _MD_LIST.sub("- ", text)
    return text


def strip_html_tags(text: str) -> str:
    """Removes HTML tags (<b>, <i>, <code>, etc.) from the text."""
    if not text:
        return text
    return _HTML_TAG.sub("", text)
