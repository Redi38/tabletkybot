"""Tests for services/ai_service/prompts.py — system prompt construction."""

from services.ai_service import system_prompt


class TestSystemPrompt:
    def test_names_ukrainian_language(self):
        assert "Ukrainian" in system_prompt("ua")

    def test_names_russian_language(self):
        assert "Russian" in system_prompt("ru")

    def test_names_english_language(self):
        assert "English" in system_prompt("en")

    def test_unknown_language_falls_back_to_generic_wording(self):
        prompt = system_prompt("fr")
        assert "the same language as the user's latest message" in prompt
