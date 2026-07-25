"""Tests for services/ai_service/formatting.py — Markdown/HTML conversion helpers."""

from services.ai_service import format_markdown_to_html, strip_html_tags


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
