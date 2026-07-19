from aiogram.fsm.context import FSMContext

from . import _ai, _common, _medicines, _prescriptions, _reports

_MODULES = (_common, _medicines, _prescriptions, _ai, _reports)

TEXTS: dict[str, dict[str, str]] = {}
for _module in _MODULES:
    for _lang, _texts in _module.TEXTS.items():
        TEXTS.setdefault(_lang, {}).update(_texts)

DEFAULT_LANG = "ua"


def get_text(lang: str, key: str, **kwargs) -> str:
    text = TEXTS.get(lang, TEXTS[DEFAULT_LANG]).get(key, f"Missing key: {key}")
    return text.format(**kwargs) if kwargs else text


def btn_variants(key: str) -> set[str]:
    return {TEXTS[lang][key] for lang in TEXTS if key in TEXTS[lang]}


def user_lang(user) -> str:
    return user.language or DEFAULT_LANG


def data_lang(data: dict) -> str:
    return data.get("lang", DEFAULT_LANG)


async def get_lang(state: FSMContext) -> str:
    data = await state.get_data()
    return data_lang(data)
