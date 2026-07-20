"""
Tests for the pure/stateless helper functions in services/ai_service.py.
These don't touch NVIDIA/Ollama APIs or the DB — they're regex/logic-only
transformations, so no mocking is needed.
"""

from services.ai_service import (
    _dedupe_tool_calls,
    _extract_known_names,
    _find_ungrounded_names,
    _looks_like_action_request,
    _resolve_language,
    detect_message_language,
    format_markdown_to_html,
    strip_html_tags,
)


class TestFormatMarkdownToHtml:
    def test_bold_conversion(self):
        assert format_markdown_to_html("**hello**") == "<b>hello</b>"

    def test_h1_h2_h3_conversion(self):
        assert format_markdown_to_html("# Title") == "<b>Title</b>\n"
        assert format_markdown_to_html("## Subtitle") == "<b>Subtitle</b>\n"
        assert format_markdown_to_html("### Small") == "<b>Small</b>\n"

    def test_list_marker_conversion(self):
        result = format_markdown_to_html("* item one")
        assert result == "- item one"

    def test_empty_string_returns_as_is(self):
        assert format_markdown_to_html("") == ""

    def test_none_returns_as_is(self):
        assert format_markdown_to_html(None) is None

    def test_plain_text_unaffected(self):
        text = "Just plain text, no markdown here."
        assert format_markdown_to_html(text) == text


class TestStripHtmlTags:
    def test_removes_bold_tags(self):
        assert strip_html_tags("<b>bold</b> text") == "bold text"

    def test_removes_multiple_tag_types(self):
        assert strip_html_tags("<b>a</b><i>b</i><code>c</code>") == "abc"

    def test_empty_string_returns_as_is(self):
        assert strip_html_tags("") == ""

    def test_no_tags_unaffected(self):
        assert strip_html_tags("no tags here") == "no tags here"


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


class TestDedupeToolCalls:
    def test_removes_exact_duplicate_calls(self):
        calls = [
            {"id": "1", "function": {"name": "get_my_medicines", "arguments": "{}"}},
            {"id": "2", "function": {"name": "get_my_medicines", "arguments": "{}"}},
        ]
        result = _dedupe_tool_calls(calls)
        assert len(result) == 1
        assert result[0]["id"] == "1"

    def test_keeps_calls_with_different_arguments(self):
        calls = [
            {"id": "1", "function": {"name": "update_medicine", "arguments": '{"id": 1}'}},
            {"id": "2", "function": {"name": "update_medicine", "arguments": '{"id": 2}'}},
        ]
        result = _dedupe_tool_calls(calls)
        assert len(result) == 2

    def test_dedupes_regardless_of_key_order_in_json(self):
        # {"a": 1, "b": 2} and {"b": 2, "a": 1} are semantically identical
        calls = [
            {"id": "1", "function": {"name": "update_medicine", "arguments": '{"a": 1, "b": 2}'}},
            {"id": "2", "function": {"name": "update_medicine", "arguments": '{"b": 2, "a": 1}'}},
        ]
        result = _dedupe_tool_calls(calls)
        assert len(result) == 1

    def test_handles_malformed_json_arguments_gracefully(self):
        calls = [
            {"id": "1", "function": {"name": "some_tool", "arguments": "not-json"}},
            {"id": "2", "function": {"name": "some_tool", "arguments": "not-json"}},
        ]
        result = _dedupe_tool_calls(calls)
        # Falls back to comparing the raw string — still dedupes identical malformed args
        assert len(result) == 1

    def test_empty_list_returns_empty(self):
        assert _dedupe_tool_calls([]) == []


class TestExtractKnownNames:
    def test_extracts_medicine_names(self):
        result = {"medicines": [{"name": "Aspirin"}, {"name": "Ibuprofen"}]}
        names = _extract_known_names("get_my_medicines", result)
        assert names == {"aspirin", "ibuprofen"}

    def test_extracts_prescription_names(self):
        result = {"prescriptions": [{"medicine_name": "Amoxicillin"}]}
        names = _extract_known_names("get_my_prescriptions", result)
        assert names == {"amoxicillin"}

    def test_unknown_tool_returns_empty_set(self):
        result = {"medicines": [{"name": "Aspirin"}]}
        names = _extract_known_names("some_other_tool", result)
        assert names == set()

    def test_empty_result_returns_empty_set(self):
        assert _extract_known_names("get_my_medicines", {}) == set()

    def test_names_are_normalized_lowercase_and_stripped(self):
        result = {"medicines": [{"name": "  Aspirin  "}]}
        names = _extract_known_names("get_my_medicines", result)
        assert names == {"aspirin"}


class TestFindUngroundedNames:
    def test_flags_name_not_in_known_set(self):
        text = "You are taking <b>Paracetamol</b> daily."
        known = {"aspirin"}
        result = _find_ungrounded_names(text, known)
        assert "paracetamol" in result

    def test_does_not_flag_known_name(self):
        text = "You are taking <b>Aspirin</b> daily."
        known = {"aspirin"}
        result = _find_ungrounded_names(text, known)
        assert result == []

    def test_empty_known_names_skips_check(self):
        # If we have no known names to compare against (e.g. no read-tool was
        # called yet), we can't meaningfully flag anything as "ungrounded".
        text = "You are taking <b>Anything</b> daily."
        result = _find_ungrounded_names(text, known_names=set())
        assert result == []

    def test_partial_match_is_not_flagged(self):
        # "Aspirin 500mg" mentioned in bold should count as grounded if the
        # known name "aspirin" is a substring of it.
        text = "Take <b>Aspirin 500mg</b> now."
        known = {"aspirin"}
        result = _find_ungrounded_names(text, known)
        assert result == []

    def test_ignores_short_bold_fragments(self):
        # Bold fragments under 3 chars are skipped (likely not a medicine name)
        text = "<b>ok</b> <b>Fakename</b>"
        known = {"aspirin"}
        result = _find_ungrounded_names(text, known)
        assert "ok" not in result
        assert "fakename" in result
