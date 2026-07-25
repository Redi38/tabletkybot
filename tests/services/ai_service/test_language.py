"""Tests for services/ai_service/language.py — language detection + action-intent heuristics."""

from services.ai_service import _looks_like_action_request, _resolve_language, detect_message_language


class TestDetectMessageLanguage:
    def test_detects_ukrainian_by_unique_chars(self):
        assert detect_message_language("Привіт, як справи?") == "ua"

    def test_detects_russian_by_unique_chars(self):
        assert detect_message_language("Привет, ещё раз") == "ru"

    def test_ambiguous_mixed_chars_returns_none(self):
        # contains both an UA-only char (і) and an RU-only char (ё)
        text = "привіт ещё"
        assert detect_message_language(text) is None

    def test_detects_english(self):
        assert detect_message_language("Hello, how are you?") == "en"

    def test_empty_string_returns_none(self):
        assert detect_message_language("") is None

    def test_none_input_returns_none(self):
        assert detect_message_language(None) is None

    def test_cyrillic_without_unique_chars_returns_none(self):
        text = "привет мир"
        assert detect_message_language(text) is None


class TestResolveLanguage:
    def test_uses_last_user_message(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Привіт"},
            {"role": "user", "content": "Привіт, як справи?"},
        ]
        assert _resolve_language(messages, fallback="en") == "ua"

    def test_falls_back_when_no_user_message(self):
        messages = [{"role": "assistant", "content": "Привіт"}]
        assert _resolve_language(messages, fallback="ru") == "ru"

    def test_falls_back_on_ambiguous_language(self):
        messages = [{"role": "user", "content": "123 456"}]
        assert _resolve_language(messages, fallback="ua") == "ua"


class TestLooksLikeActionRequest:
    def test_detects_ukrainian_action_keyword(self):
        assert _looks_like_action_request("Додай ібупрофен") is True

    def test_detects_russian_action_keyword(self):
        assert _looks_like_action_request("Удали это лекарство") is True

    def test_detects_english_action_keyword(self):
        assert _looks_like_action_request("Please add a new medicine") is True

    def test_plain_question_without_keywords(self):
        assert _looks_like_action_request("Дякую, все зрозуміло") is False

    def test_empty_string_returns_false(self):
        assert _looks_like_action_request("") is False

    def test_case_insensitive(self):
        assert _looks_like_action_request("ДОДАЙ ліки") is True
