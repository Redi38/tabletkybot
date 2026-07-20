"""
Tests for admin/app.py's own routes: /health and /favicon.ico.

DashboardAdmin.index (the /admin/ dashboard page) is covered in
test_dashboard.py alongside the other dashboard routes, since it renders
the same template with the same stats data.
"""

from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from admin.app import app


class TestFavicon:
    def test_returns_the_favicon_file(self):
        client = TestClient(app)
        response = client.get("/favicon.ico")
        assert response.status_code == 200
        assert response.headers["content-type"] in ("image/vnd.microsoft.icon", "image/x-icon")


class TestHealthCheck:
    def test_returns_200_when_db_and_redis_are_ok(self):
        client = TestClient(app)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.return_value = mock_session

        mock_redis_client = AsyncMock()

        with (
            patch("admin.app.SessionLocal", return_value=mock_session_cm),
            patch("admin.app.aioredis.from_url", return_value=mock_redis_client),
        ):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["checks"]["database"] == "ok"
        assert data["checks"]["redis"] == "ok"

    def test_returns_503_when_database_is_down(self):
        client = TestClient(app)

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.side_effect = ConnectionError("could not connect to server")

        mock_redis_client = AsyncMock()

        with (
            patch("admin.app.SessionLocal", return_value=mock_session_cm),
            patch("admin.app.aioredis.from_url", return_value=mock_redis_client),
        ):
            response = client.get("/health")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert "error" in data["checks"]["database"]
        assert data["checks"]["redis"] == "ok"

    def test_returns_503_when_redis_is_down(self):
        client = TestClient(app)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.return_value = mock_session

        mock_redis_client = AsyncMock()
        mock_redis_client.ping.side_effect = ConnectionError("redis unreachable")

        with (
            patch("admin.app.SessionLocal", return_value=mock_session_cm),
            patch("admin.app.aioredis.from_url", return_value=mock_redis_client),
        ):
            response = client.get("/health")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "unhealthy"
        assert data["checks"]["database"] == "ok"
        assert "error" in data["checks"]["redis"]

    def test_returns_503_when_both_are_down(self):
        client = TestClient(app)

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.side_effect = ConnectionError("db down")

        with (
            patch("admin.app.SessionLocal", return_value=mock_session_cm),
            patch("admin.app.aioredis.from_url", side_effect=ConnectionError("redis down")),
        ):
            response = client.get("/health")

        assert response.status_code == 503
        data = response.json()
        assert "error" in data["checks"]["database"]
        assert "error" in data["checks"]["redis"]
