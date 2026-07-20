"""
Tests for admin/dashboard.py: /admin/dashboard, /api/admin/stats,
/api/admin/ai-metrics, and the AIMetricsView page.
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


class TestAIMetricsEndpoint:
    def test_returns_summary_and_recent_calls(self):
        client = TestClient(app)

        class FakeMetric:
            id = 1
            model_used = "claude-sonnet-4-6"
            tool_choice = "auto"
            tool_names = "add_medicine,list_medicines"
            latency_ms = 842

            class _Status:
                pass

            status = "success"

            from datetime import datetime

            created_at = datetime(2026, 7, 19, 10, 30, 0)

        fake_summary = {"total_calls": 5, "avg_latency_ms": 500.0, "by_status": {"success": 5}}

        mock_session = AsyncMock()
        with (
            patch("admin.dashboard.SessionLocal", return_value=_mock_session_local(mock_session)),
            patch("admin.dashboard.crud.get_ai_metrics_summary", AsyncMock(return_value=fake_summary)),
            patch(
                "admin.dashboard.crud.get_recent_ai_metrics",
                AsyncMock(return_value=[(FakeMetric(), "Redi Shershnov")]),
            ),
        ):
            response = client.get("/api/admin/ai-metrics")

        assert response.status_code == 200
        data = response.json()
        assert data["summary"] == fake_summary
        assert data["recent"][0]["full_name"] == "Redi Shershnov"
        assert data["recent"][0]["model_used"] == "claude-sonnet-4-6"
        assert data["recent"][0]["tool_names"] == "add_medicine,list_medicines"

    def test_shows_dash_when_no_tool_names(self):
        client = TestClient(app)

        class FakeMetric:
            id = 1
            model_used = "m"
            tool_choice = None
            tool_names = None
            latency_ms = 1
            status = "success"

            from datetime import datetime

            created_at = datetime(2026, 7, 19, 10, 30, 0)

        mock_session = AsyncMock()
        with (
            patch("admin.dashboard.SessionLocal", return_value=_mock_session_local(mock_session)),
            patch("admin.dashboard.crud.get_ai_metrics_summary", AsyncMock(return_value={})),
            patch(
                "admin.dashboard.crud.get_recent_ai_metrics",
                AsyncMock(return_value=[(FakeMetric(), "Someone")]),
            ),
        ):
            response = client.get("/api/admin/ai-metrics")

        assert response.json()["recent"][0]["tool_names"] == "—"


class TestAIMetricsView:
    def test_page_requires_login(self):
        client = TestClient(app, follow_redirects=False)
        response = client.get("/admin/ai-metrics-view")
        assert response.status_code in (302, 307)
