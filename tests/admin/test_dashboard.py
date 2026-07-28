"""
Tests for admin/dashboard.py: /admin/dashboard and /api/admin/stats.
"""

from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from admin.app import app


def _mock_session_local(mock_session):
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_session
    return mock_cm


class TestAdminStatsEndpoint:
    def test_returns_stats_for_requested_period(self):
        client = TestClient(app)
        fake_stats = {"taken": 10, "skipped": 2, "adherence_pct": 83.3}

        mock_session = AsyncMock()
        with (
            patch("admin.dashboard.SessionLocal", return_value=_mock_session_local(mock_session)),
            patch("admin.dashboard.crud.get_dashboard_stats", AsyncMock(return_value=fake_stats)) as mock_get_stats,
        ):
            response = client.get("/api/admin/stats?period=7d")

        assert response.status_code == 200
        assert response.json() == fake_stats
        mock_get_stats.assert_awaited_once_with(mock_session, "7d")

    def test_defaults_to_all_time_period(self):
        client = TestClient(app)

        mock_session = AsyncMock()
        with (
            patch("admin.dashboard.SessionLocal", return_value=_mock_session_local(mock_session)),
            patch("admin.dashboard.crud.get_dashboard_stats", AsyncMock(return_value={})) as mock_get_stats,
        ):
            client.get("/api/admin/stats")

        mock_get_stats.assert_awaited_once_with(mock_session, "all")
