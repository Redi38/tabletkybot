"""
Login-based authentication for the SQLAdmin panel.
"""

import hashlib
import hmac
import logging
import secrets

from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 260_000
_SESSION_KEY = "admin_authenticated"


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage in ADMIN_PANEL_PASSWORD_HASH."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verification of a plaintext password against a stored hash."""
    try:
        algorithm, iterations_str, salt, hash_hex = stored_hash.split("$")
    except ValueError:
        return False

    if algorithm != _ALGORITHM:
        return False

    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations_str))
    return hmac.compare_digest(digest.hex(), hash_hex)


class AdminAuth(AuthenticationBackend):
    """Session-cookie based auth backend for SQLAdmin.

    Credentials are compared using constant-time comparisons to avoid
    leaking information via response-timing side channels.
    """

    def __init__(self, secret_key: str, username: str, password_hash: str):
        super().__init__(secret_key=secret_key)
        self._username = username
        self._password_hash = password_hash

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))

        username_ok = hmac.compare_digest(username, self._username)
        password_ok = verify_password(password, self._password_hash) if self._password_hash else False

        if not (username_ok and password_ok):
            logger.warning("Failed admin panel login attempt for username=%r", username)
            return False

        request.session[_SESSION_KEY] = True
        logger.info("Admin panel login successful for username=%r", username)
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> Response | bool:
        return bool(request.session.get(_SESSION_KEY))


def _main() -> None:
    """CLI helper: `python -m admin.auth` prompts for a password and prints
    the hash to put into ADMIN_PANEL_PASSWORD_HASH."""
    import getpass

    password = getpass.getpass("New admin panel password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        raise SystemExit(1)
    if len(password) < 12:
        print("Warning: password is shorter than 12 characters.")

    print("\nADMIN_PANEL_PASSWORD_HASH=" + hash_password(password))
    print("ADMIN_PANEL_SESSION_SECRET=" + secrets.token_hex(32))


if __name__ == "__main__":
    _main()
