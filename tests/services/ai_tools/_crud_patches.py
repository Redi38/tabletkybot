"""
Shared test helpers for services/ai_tools tests.

_patch_crud_get_user_medicines / _patch_crud_get_user_prescriptions are
context managers that monkeypatch database.crud so execute_* functions
can be tested without hitting a real DB.
"""

from unittest.mock import AsyncMock


def _patch_crud_get_user_medicines(session, medicines):
    """Context manager that patches database.crud.get_user_medicines for the duration of a test."""
    import database.crud as crud_module

    class _Patch:
        def __enter__(self):
            self._original = crud_module.get_user_medicines
            crud_module.get_user_medicines = AsyncMock(return_value=medicines)
            return self

        def __exit__(self, *args):
            crud_module.get_user_medicines = self._original

    return _Patch()


def _patch_crud_get_user_prescriptions(prescriptions):
    import database.crud as crud_module

    class _Patch:
        def __enter__(self):
            self._original = crud_module.get_user_prescriptions
            crud_module.get_user_prescriptions = AsyncMock(return_value=prescriptions)
            return self

        def __exit__(self, *args):
            crud_module.get_user_prescriptions = self._original

    return _Patch()
