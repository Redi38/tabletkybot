"""Tests for the internal /api/sync endpoint's shared-secret authentication
(main.py::build_sync_handler)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import main as main_module


@pytest.fixture
def patched_sync(monkeypatch):
    """Avoids hitting real DB/reminder logic — we're only testing the auth layer."""
    monkeypatch.setattr(main_module, "sync_reminders", AsyncMock())
    monkeypatch.setattr(main_module, "sync_single_reminder", AsyncMock())


async def _post(client, secret_header: str | None, payload: dict | None = None):
    headers = {"X-Sync-Secret": secret_header} if secret_header is not None else {}
    return await client.post("/api/sync", json=payload or {"action": "add", "medicine_id": 5}, headers=headers)


@pytest.mark.asyncio
async def test_rejects_missing_secret_header(patched_sync):
    handler = main_module.build_sync_handler(MagicMock(), MagicMock(), "topsecret123")
    app = web.Application()
    app.router.add_post("/api/sync", handler)
    async with TestClient(TestServer(app)) as client:
        resp = await _post(client, secret_header=None)
        assert resp.status == 401
        assert (await resp.json())["status"] == "error"


@pytest.mark.asyncio
async def test_rejects_wrong_secret(patched_sync):
    handler = main_module.build_sync_handler(MagicMock(), MagicMock(), "topsecret123")
    app = web.Application()
    app.router.add_post("/api/sync", handler)
    async with TestClient(TestServer(app)) as client:
        resp = await _post(client, secret_header="wrong-value")
        assert resp.status == 401


@pytest.mark.asyncio
async def test_accepts_correct_secret(patched_sync):
    handler = main_module.build_sync_handler(MagicMock(), MagicMock(), "topsecret123")
    app = web.Application()
    app.router.add_post("/api/sync", handler)
    async with TestClient(TestServer(app)) as client:
        resp = await _post(client, secret_header="topsecret123")
        assert resp.status == 200
        assert (await resp.json())["status"] == "success"
        main_module.sync_single_reminder.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_closed_when_secret_not_configured(patched_sync):
    """An empty/unset SYNC_SECRET must reject every request, including one
    with an empty header — never silently allow all requests through."""
    handler = main_module.build_sync_handler(MagicMock(), MagicMock(), "")
    app = web.Application()
    app.router.add_post("/api/sync", handler)
    async with TestClient(TestServer(app)) as client:
        resp = await _post(client, secret_header="")
        assert resp.status == 401
        resp = await _post(client, secret_header="anything-at-all")
        assert resp.status == 401
