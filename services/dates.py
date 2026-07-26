"""
Canonical flexible date parser, shared by handlers/prescriptions/utils.py
(user-typed dates like "15.03.26") and services/ai_tools (dates supplied by
the AI agent, which may also come back in ISO format).
"""

from datetime import date, datetime

_DATE_FORMATS = ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d")


def parse_date_flexible(text: str) -> date | None:
    text = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
