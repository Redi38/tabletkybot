"""
Tests for locales/__init__.py: get_text() and btn_variants().
"""

import pytest

import locales
from locales import texts


@pytest.fixture
def sample_texts(monkeypatch):
    """A small, deterministic TEXTS dict for isolated unit tests."""
    data = {
        "ua": {
            "btn_settings": "Налаштування",
            "greeting": "Привіт, {name}!",
            "only_ua_key": "Тільки українською",
        },
        "en": {
            "btn_settings": "Settings",
            "greeting": "Hello, {name}!",
        },
        "ru": {
            "btn_settings": "Настройки",
            "greeting": "Привет, {name}!",
        },
    }
    monkeypatch.setattr(locales, "TEXTS", data)
    return data


# get_text


def test_get_text_returns_correct_language(sample_texts):
    assert texts.get_text("en", "btn_settings") == "Settings"
    assert texts.get_text("ru", "btn_settings") == "Настройки"
    assert texts.get_text("ua", "btn_settings") == "Налаштування"


def test_get_text_formats_kwargs(sample_texts):
    result = texts.get_text("en", "greeting", name="Redi")
    assert result == "Hello, Redi!"


def test_get_text_no_kwargs_does_not_call_format(sample_texts):
    """A key with literal { } but no kwargs passed should be returned as-is,
    not crash trying to .format() with missing placeholders."""
    result = texts.get_text("en", "greeting")
    assert result == "Hello, {name}!"


def test_get_text_missing_key_returns_placeholder(sample_texts):
    result = texts.get_text("en", "nonexistent_key")
    assert result == "Missing key: nonexistent_key"


def test_get_text_unknown_language_falls_back_to_ua(sample_texts):
    result = texts.get_text("fr", "btn_settings")
    assert result == "Налаштування"


def test_get_text_key_missing_in_requested_lang_but_present_in_ua(sample_texts):
    """A key that only exists in 'ua' - requesting it in 'en' should show
    the missing-key placeholder, NOT silently fall back to Ukrainian text
    (only unknown *languages* fall back to 'ua', not missing individual keys)."""
    result = texts.get_text("en", "only_ua_key")
    assert result == "Missing key: only_ua_key"


# btn_variants


def test_btn_variants_collects_all_language_variants(sample_texts):
    result = texts.btn_variants("btn_settings")
    assert result == {"Налаштування", "Settings", "Настройки"}


def test_btn_variants_missing_key_returns_empty_set(sample_texts):
    result = texts.btn_variants("nonexistent_key")
    assert result == set()


def test_btn_variants_key_present_in_only_one_language(sample_texts):
    result = texts.btn_variants("only_ua_key")
    assert result == {"Тільки українською"}


def test_btn_variants_returns_a_set_not_a_list(sample_texts):
    result = texts.btn_variants("btn_settings")
    assert isinstance(result, set)


# Sanity checks against the real merged texts (from _common.py etc.)


def test_real_texts_has_all_three_languages():
    assert {"ua", "en", "ru"}.issubset(texts.TEXTS.keys())


def test_real_get_text_known_common_key():
    assert texts.get_text("ua", "btn_settings") == "👤 Налаштування"
    assert texts.get_text("en", "btn_settings") == "👤 Settings"
    assert texts.get_text("ru", "btn_settings") == "👤 Настройки"


def test_real_get_text_formats_settings_title():
    result = texts.get_text("en", "settings_title", name="Redi", tz="Europe/Kyiv")
    assert "Redi" in result
    assert "Europe/Kyiv" in result


def test_real_btn_variants_btn_settings_has_three_unique_variants():
    result = texts.btn_variants("btn_settings")
    assert result == {"👤 Налаштування", "👤 Settings", "👤 Настройки"}


def test_real_feedback_admin_header_formats_all_placeholders():
    result = texts.get_text(
        "ua",
        "feedback_admin_header",
        name="Redi",
        username="redi_dev",
        user_id=123,
        text="test feedback",
    )
    assert "Redi" in result
    assert "redi_dev" in result
    assert "123" in result
    assert "test feedback" in result


class TestDataLang:
    def test_returns_lang_when_present(self):
        assert texts.data_lang({"lang": "en"}) == "en"

    def test_defaults_to_ua_when_missing(self):
        assert texts.data_lang({}) == "ua"

    def test_ignores_unrelated_keys(self):
        assert texts.data_lang({"medicine_id": 5, "lang": "ru"}) == "ru"


class TestUserLang:
    def test_returns_language_when_set(self):
        class FakeUser:
            language = "en"

        assert texts.user_lang(FakeUser()) == "en"

    def test_defaults_to_ua_when_none(self):
        class FakeUser:
            language = None

        assert texts.user_lang(FakeUser()) == "ua"

    def test_defaults_to_ua_when_empty_string(self):
        class FakeUser:
            language = ""

        assert texts.user_lang(FakeUser()) == "ua"


class TestGetLang:
    async def test_reads_lang_from_fsm_state(self):
        class FakeState:
            async def get_data(self):
                return {"lang": "en"}

        result = await texts.get_lang(FakeState())
        assert result == "en"

    async def test_defaults_to_ua_when_state_has_no_lang(self):
        class FakeState:
            async def get_data(self):
                return {}

        result = await texts.get_lang(FakeState())
        assert result == "ua"
