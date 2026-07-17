"""
Tests for admin/auth.py: password hashing and the SQLAdmin login backend.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from admin.auth import AdminAuth, hash_password, verify_password


class TestPasswordHashing:
    def test_verify_correct_password(self):
        stored = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", stored) is True

    def test_verify_wrong_password(self):
        stored = hash_password("correct-horse-battery-staple")
        assert verify_password("wrong-password", stored) is False

    def test_hash_is_salted_and_not_reversible_by_equality(self):
        # Hashing the same password twice must not produce the same output
        # (random salt), and must never equal the plaintext.
        first = hash_password("same-password")
        second = hash_password("same-password")
        assert first != second
        assert "same-password" not in first

    def test_verify_rejects_malformed_hash(self):
        assert verify_password("anything", "not-a-valid-hash") is False

    def test_verify_rejects_unknown_algorithm(self):
        stored = hash_password("secret").replace("pbkdf2_sha256", "md5")
        assert verify_password("secret", stored) is False


class TestAdminAuthBackend:
    def _make_backend(self, password: str = "correct-horse-battery-staple") -> AdminAuth:
        return AdminAuth(
            secret_key="test-secret",
            username="admin",
            password_hash=hash_password(password),
        )

    def _make_request(self, form_data: dict | None = None):
        request = MagicMock()
        request.session = {}
        if form_data is not None:
            request.form = AsyncMock(return_value=form_data)
        return request

    @pytest.mark.asyncio
    async def test_login_succeeds_with_correct_credentials(self):
        backend = self._make_backend()
        request = self._make_request({"username": "admin", "password": "correct-horse-battery-staple"})

        result = await backend.login(request)

        assert result is True
        assert request.session["admin_authenticated"] is True

    @pytest.mark.asyncio
    async def test_login_fails_with_wrong_password(self):
        backend = self._make_backend()
        request = self._make_request({"username": "admin", "password": "wrong"})

        result = await backend.login(request)

        assert result is False
        assert "admin_authenticated" not in request.session

    @pytest.mark.asyncio
    async def test_login_fails_with_wrong_username(self):
        backend = self._make_backend()
        request = self._make_request({"username": "someone_else", "password": "correct-horse-battery-staple"})

        result = await backend.login(request)

        assert result is False

    @pytest.mark.asyncio
    async def test_login_fails_when_no_password_hash_configured(self):
        # Fail-closed: an empty ADMIN_PANEL_PASSWORD_HASH must reject every
        # login attempt rather than silently accepting one.
        backend = AdminAuth(secret_key="test-secret", username="admin", password_hash="")
        request = self._make_request({"username": "admin", "password": "anything"})

        result = await backend.login(request)

        assert result is False

    @pytest.mark.asyncio
    async def test_authenticate_true_when_session_flag_set(self):
        backend = self._make_backend()
        request = self._make_request()
        request.session = {"admin_authenticated": True}

        assert await backend.authenticate(request) is True

    @pytest.mark.asyncio
    async def test_authenticate_false_when_session_empty(self):
        backend = self._make_backend()
        request = self._make_request()
        request.session = {}

        assert await backend.authenticate(request) is False

    @pytest.mark.asyncio
    async def test_logout_clears_session(self):
        backend = self._make_backend()
        request = self._make_request()
        session = {"admin_authenticated": True}
        request.session = session

        result = await backend.logout(request)

        assert result is True
        assert session == {}
