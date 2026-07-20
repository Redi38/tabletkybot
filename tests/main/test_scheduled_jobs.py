"""Tests for the internal /api/scheduled-jobs endpoint
(main.py::build_scheduled_jobs_handler)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import main as main_module
from database.models import Base, User


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_rejects_missing_secret(session_factory):
    handler = main_module.build_scheduled_jobs_handler(session_factory, "topsecret123")
    app = web.Application()
    app.router.add_get("/api/scheduled-jobs", handler)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/scheduled-jobs")
        assert resp.status == 401


@pytest.mark.asyncio
async def test_rejects_wrong_secret(session_factory):
    handler = main_module.build_scheduled_jobs_handler(session_factory, "topsecret123")
    app = web.Application()
    app.router.add_get("/api/scheduled-jobs", handler)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/scheduled-jobs", headers={"X-Sync-Secret": "wrong"})
        assert resp.status == 401


@pytest.mark.asyncio
async def test_fail_closed_when_secret_not_configured(session_factory):
    handler = main_module.build_scheduled_jobs_handler(session_factory, "")
    app = web.Application()
    app.router.add_get("/api/scheduled-jobs", handler)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/scheduled-jobs", headers={"X-Sync-Secret": ""})
        assert resp.status == 401


@pytest.mark.asyncio
async def test_resolves_username_and_sorts_by_sent_at(session_factory):
    async with session_factory() as s:
        s.add(User(id=12345, username="nikita_dev", full_name="Nikita Shershnov"))
        s.add(User(id=99999, username=None, full_name="Anonymous User"))
        await s.commit()

    fake_active = [
        {
            "chat_id": 12345,
            "medicine_id": 5,
            "medicine_name": "Ibuprofen",
            "sent_at": datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc).isoformat(),
        },
        {
            "chat_id": 99999,
            "medicine_id": 7,
            "medicine_name": "Paracetamol",
            "sent_at": datetime(2026, 7, 18, 9, 30, tzinfo=timezone.utc).isoformat(),
        },
    ]

    with patch.object(main_module, "get_active_pending_reminders", AsyncMock(return_value=fake_active)):
        handler = main_module.build_scheduled_jobs_handler(session_factory, "topsecret123")
        app = web.Application()
        app.router.add_get("/api/scheduled-jobs", handler)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/scheduled-jobs", headers={"X-Sync-Secret": "topsecret123"})
            assert resp.status == 200
            data = await resp.json()

    assert data["status"] == "success"
    assert data["count"] == 2
    # Paracetamol was sent earlier (09:30) than Ibuprofen (10:00) -> should come first
    assert data["jobs"][0]["medicine_name"] == "Paracetamol"
    assert data["jobs"][0]["user_name"] == "Anonymous User"
    assert data["jobs"][1]["medicine_name"] == "Ibuprofen"
    assert data["jobs"][1]["user_name"] == "Nikita Shershnov"


@pytest.mark.asyncio
async def test_falls_back_to_chat_id_when_user_not_in_db(session_factory):
    fake_active = [
        {
            "chat_id": 55555,
            "medicine_id": 1,
            "medicine_name": "Vitamin D",
            "sent_at": datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc).isoformat(),
        }
    ]

    with patch.object(main_module, "get_active_pending_reminders", AsyncMock(return_value=fake_active)):
        handler = main_module.build_scheduled_jobs_handler(session_factory, "topsecret123")
        app = web.Application()
        app.router.add_get("/api/scheduled-jobs", handler)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/scheduled-jobs", headers={"X-Sync-Secret": "topsecret123"})
            data = await resp.json()

    assert data["jobs"][0]["user_name"] == "55555"


@pytest.mark.asyncio
async def test_returns_empty_list_when_nothing_active(session_factory):
    with patch.object(main_module, "get_active_pending_reminders", AsyncMock(return_value=[])):
        handler = main_module.build_scheduled_jobs_handler(session_factory, "topsecret123")
        app = web.Application()
        app.router.add_get("/api/scheduled-jobs", handler)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/scheduled-jobs", headers={"X-Sync-Secret": "topsecret123"})
            data = await resp.json()

    assert data["count"] == 0
    assert data["jobs"] == []
