"""
Tests for services/scheduler/redis_state.py: the pending-reminder
save/get roundtrip (including malformed-JSON recovery), the stock-alert
pending store, and the take/skip action lock used to guard against
duplicate button taps — plus the "no Redis client configured" early
return each function falls back to when the bot runs without Redis.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from services.scheduler import acquire_action_lock
from services.scheduler import redis_state as scheduler_redis_module


class TestInitRedis:
    def test_creates_a_client_from_the_given_url(self):
        fake_client = MagicMock()
        with patch(
            "services.scheduler.redis_state.aioredis.from_url", MagicMock(return_value=fake_client)
        ) as mock_from_url:
            scheduler_redis_module.init_redis("redis://localhost:6379/0")

        mock_from_url.assert_called_once_with("redis://localhost:6379/0", decode_responses=True)
        assert scheduler_redis_module._redis_client is fake_client


class TestPendingReminderRedisHelpers:
    async def test_save_and_get_roundtrip(self, mock_redis):
        stored = {}

        async def fake_set(key, value, ex=None):
            stored["key"] = key
            stored["value"] = value
            return True

        async def fake_get(key):
            return stored.get("value")

        mock_redis.set = AsyncMock(side_effect=fake_set)
        mock_redis.get = AsyncMock(side_effect=fake_get)

        await scheduler_redis_module._save_pending_reminder(
            chat_id=1,
            medicine_id=2,
            message_id=999,
            medicine_name="Aspirin",
            course_duration=5,
            language="ua",
            timezone="Europe/Kyiv",
        )
        result = await scheduler_redis_module._get_pending_reminder(chat_id=1, medicine_id=2)

        assert result["medicine_name"] == "Aspirin"
        assert result["course_duration"] == 5

    async def test_get_pending_reminder_returns_none_on_malformed_json(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="not-valid-json{")

        result = await scheduler_redis_module._get_pending_reminder(chat_id=1, medicine_id=2)

        assert result is None

    async def test_get_pending_reminder_returns_none_when_missing(self, mock_redis):
        mock_redis.get = AsyncMock(return_value=None)

        result = await scheduler_redis_module._get_pending_reminder(chat_id=1, medicine_id=2)

        assert result is None


class TestAcquireActionLock:
    async def test_first_call_acquires_lock(self, mock_redis):
        mock_redis.set = AsyncMock(return_value=True)

        acquired = await acquire_action_lock(chat_id=1, medicine_id=2)

        assert acquired is True

    async def test_second_call_within_ttl_is_rejected(self, mock_redis):
        mock_redis.set = AsyncMock(return_value=None)

        acquired = await acquire_action_lock(chat_id=1, medicine_id=2)

        assert acquired is False

    async def test_uses_correct_key_and_nx_ex_options(self, mock_redis):
        mock_redis.set = AsyncMock(return_value=True)

        await acquire_action_lock(chat_id=111, medicine_id=42)

        args, kwargs = mock_redis.set.call_args
        assert args[0] == "action_lock:111:42"
        assert kwargs.get("nx") is True
        assert kwargs.get("ex") == 3

    async def test_fails_open_when_redis_not_configured(self, mock_redis):
        scheduler_redis_module._redis_client = None

        acquired = await acquire_action_lock(chat_id=1, medicine_id=2)

        assert acquired is True


class TestNoRedisClientEarlyReturns:
    """
    Every write/read helper in this module fails open (returns None/[]/True
    without raising) when the bot is running without Redis configured.
    """

    async def test_save_pending_reminder_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(scheduler_redis_module, "_redis_client", None)
        await scheduler_redis_module._save_pending_reminder(1, 2, 3, "Aspirin", 5, "en", "Europe/Kyiv")

    async def test_get_pending_reminder_returns_none(self, monkeypatch):
        monkeypatch.setattr(scheduler_redis_module, "_redis_client", None)
        assert await scheduler_redis_module._get_pending_reminder(1, 2) is None

    async def test_delete_pending_reminder_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(scheduler_redis_module, "_redis_client", None)
        await scheduler_redis_module._delete_pending_reminder(1, 2)

    async def test_get_all_pending_reminders_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(scheduler_redis_module, "_redis_client", None)
        assert await scheduler_redis_module._get_all_pending_reminders() == []

    async def test_delete_pending_reminders_for_medicine_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(scheduler_redis_module, "_redis_client", None)
        await scheduler_redis_module._delete_pending_reminders_for_medicine(2)

    async def test_save_stock_alert_pending_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(scheduler_redis_module, "_redis_client", None)
        await scheduler_redis_module.save_stock_alert_pending(1, 2, "Aspirin", "en")

    async def test_get_stock_alert_pending_returns_none(self, monkeypatch):
        monkeypatch.setattr(scheduler_redis_module, "_redis_client", None)
        assert await scheduler_redis_module.get_stock_alert_pending(1, 2) is None

    async def test_clear_stock_alert_pending_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(scheduler_redis_module, "_redis_client", None)
        await scheduler_redis_module.clear_stock_alert_pending(1, 2)

    async def test_delete_stock_alerts_for_medicine_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(scheduler_redis_module, "_redis_client", None)
        await scheduler_redis_module._delete_stock_alerts_for_medicine(2)


class TestGetAllPendingRemindersParsing:
    async def test_skips_a_malformed_key_without_raising(self, mock_redis):
        async def _scan_iter(match=None):
            yield "pending_reminder:not-an-int:2"
            yield "pending_reminder:1:2"

        mock_redis.scan_iter = _scan_iter
        mock_redis.get = AsyncMock(return_value=json.dumps({"medicine_name": "Aspirin"}))

        result = await scheduler_redis_module._get_all_pending_reminders()

        assert result == [(1, 2, {"medicine_name": "Aspirin"})]

    async def test_skips_an_entry_whose_stored_value_is_corrupted_json(self, mock_redis):
        async def _scan_iter(match=None):
            yield "pending_reminder:1:2"

        mock_redis.scan_iter = _scan_iter
        mock_redis.get = AsyncMock(return_value="{not valid json")

        assert await scheduler_redis_module._get_all_pending_reminders() == []


class TestGetActivePendingReminders:
    async def test_shapes_the_output_for_the_admin_panel(self, mock_redis):
        async def _scan_iter(match=None):
            yield "pending_reminder:1:2"

        mock_redis.scan_iter = _scan_iter
        mock_redis.get = AsyncMock(
            return_value=json.dumps({"medicine_name": "Aspirin", "sent_at": "2026-07-20T09:00:00+00:00"})
        )

        result = await scheduler_redis_module.get_active_pending_reminders()

        assert result == [
            {"chat_id": 1, "medicine_id": 2, "medicine_name": "Aspirin", "sent_at": "2026-07-20T09:00:00+00:00"}
        ]


class TestDeletePendingRemindersForMedicine:
    async def test_deletes_all_matching_keys(self, mock_redis):
        async def _scan_iter(match=None):
            for key in ["pending_reminder:1:2", "pending_reminder:5:2"]:
                yield key

        mock_redis.scan_iter = _scan_iter

        await scheduler_redis_module._delete_pending_reminders_for_medicine(2)

        assert mock_redis.delete.await_count == 2


class TestStockAlertPending:
    async def test_save_and_get_roundtrip(self, mock_redis):
        stored = {}

        async def fake_set(key, value, ex=None):
            stored["key"], stored["value"] = key, value
            return True

        async def fake_get(key):
            return stored.get("value")

        mock_redis.set = AsyncMock(side_effect=fake_set)
        mock_redis.get = AsyncMock(side_effect=fake_get)

        await scheduler_redis_module.save_stock_alert_pending(1, 2, "Aspirin", "en")
        result = await scheduler_redis_module.get_stock_alert_pending(1, 2)

        assert result == {"medicine_name": "Aspirin", "language": "en"}
        assert stored["key"] == "stock_alert_pending:1:2"

    async def test_get_returns_none_when_missing(self, mock_redis):
        assert await scheduler_redis_module.get_stock_alert_pending(1, 2) is None

    async def test_get_returns_none_on_corrupted_json(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="{not valid json")
        assert await scheduler_redis_module.get_stock_alert_pending(1, 2) is None

    async def test_clear_deletes_the_key(self, mock_redis):
        await scheduler_redis_module.clear_stock_alert_pending(1, 2)
        mock_redis.delete.assert_awaited_once_with("stock_alert_pending:1:2")


class TestDeleteStockAlertsForMedicine:
    async def test_deletes_all_matching_keys(self, mock_redis):
        async def _scan_iter(match=None):
            for key in ["stock_alert_pending:1:2", "stock_alert_pending:5:2"]:
                yield key

        mock_redis.scan_iter = _scan_iter

        await scheduler_redis_module._delete_stock_alerts_for_medicine(2)

        assert mock_redis.delete.await_count == 2
