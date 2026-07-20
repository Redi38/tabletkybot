"""
Tests for admin/sync.py: notify_bot() (fire-and-forget POST to the bot),
the /api/admin/scheduled-jobs proxy, and the ReminderQueueView page.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

from admin.app import app
from admin.sync import notify_bot


def _mock_client_session_for_post():
    """aiohttp.ClientSession() used as `async with ... as session`, where
    `await session.post(...)` is awaited directly (not itself a context
    manager) — matches notify_bot's actual usage."""
    mock_session = AsyncMock()
    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)
    return mock_session_cm, mock_session


def _mock_client_session_for_get(status: int, json_data: dict):
    """aiohttp.ClientSession() used as `async with ... as session`, where
    `session.get(...)` is ITSELF used as `async with session.get(...) as
    resp` — matches get_scheduled_jobs's actual usage."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=json_data)
    mock_resp_cm = MagicMock()
    mock_resp_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp_cm.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp_cm)
    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)
    return mock_session_cm, mock_session


class TestNotifyBot:
    async def test_sends_post_with_correct_payload_and_secret_header(self):
        mock_session_cm, mock_session = _mock_client_session_for_post()

        with (
            patch("admin.sync.aiohttp.ClientSession", return_value=mock_session_cm),
            patch("admin.sync.config") as mock_config,
        ):
            mock_config.sync_secret = "topsecret"
            await notify_bot("update", medicine_id=5)

        args, kwargs = mock_session.post.call_args
        assert kwargs["json"] == {"action": "update", "medicine_id": 5}
        assert kwargs["headers"] == {"X-Sync-Secret": "topsecret"}

    async def test_does_not_raise_when_bot_is_unreachable(self):
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=ConnectionError("connection refused"))
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("admin.sync.aiohttp.ClientSession", return_value=mock_session_cm):
            # Should not raise
            await notify_bot("update", medicine_id=5)


class TestScheduledJobsProxy:
    def test_returns_data_on_success(self):
        client = TestClient(app)
        fake_data = {"status": "success", "count": 1, "jobs": [{"medicine_name": "Ibuprofen"}]}
        mock_session_cm, _ = _mock_client_session_for_get(status=200, json_data=fake_data)

        with patch("admin.sync.aiohttp.ClientSession", return_value=mock_session_cm):
            response = client.get("/api/admin/scheduled-jobs")

        assert response.status_code == 200
        assert response.json() == fake_data

    def test_returns_error_when_bot_responds_with_non_200(self):
        client = TestClient(app)
        mock_session_cm, _ = _mock_client_session_for_get(
            status=401, json_data={"status": "error", "message": "unauthorized"}
        )

        with patch("admin.sync.aiohttp.ClientSession", return_value=mock_session_cm):
            response = client.get("/api/admin/scheduled-jobs")

        assert response.status_code == 200  # the proxy endpoint itself still returns 200
        data = response.json()
        assert data["status"] == "error"
        assert data["message"] == "unauthorized"

    def test_returns_error_when_bot_is_unreachable(self):
        client = TestClient(app)
        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=ConnectionError("connection refused"))
        mock_session_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("admin.sync.aiohttp.ClientSession", return_value=mock_session_cm):
            response = client.get("/api/admin/scheduled-jobs")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "Could not reach the bot" in data["message"]


class TestReminderQueueView:
    def test_page_requires_login(self):
        client = TestClient(app, follow_redirects=False)
        response = client.get("/admin/reminders-view")
        assert response.status_code in (302, 307)
