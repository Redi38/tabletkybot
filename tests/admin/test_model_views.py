"""
Tests for admin/model_views.py.

Must import `admin.app` first (before `admin.model_views`) — model_views
imports notify_bot from admin.sync, which imports `app` from admin.app,
which at its own bottom imports admin.model_views to register the views.
Importing admin.model_views directly and first hits that partially-
initialized module mid-import. Going through admin.app first (as every
other module in this package already does at real runtime) avoids it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from admin.app import app  # noqa: F401 — import order matters, see module docstring
from admin.model_views import ChatHistoryAdmin, MedicineAdmin, MedicineScheduleAdmin


class TestMedicineAdminHooks:
    async def test_after_model_change_notifies_bot_with_update(self):
        view = MedicineAdmin()
        fake_model = MagicMock(id=42)

        with patch("admin.model_views.notify_bot", AsyncMock()) as mock_notify:
            await view.after_model_change({}, fake_model, is_created=True, request=None)

        mock_notify.assert_awaited_once_with("update", 42)

    async def test_after_model_delete_notifies_bot_with_delete(self):
        view = MedicineAdmin()
        fake_model = MagicMock(id=42)

        with patch("admin.model_views.notify_bot", AsyncMock()) as mock_notify:
            await view.after_model_delete(fake_model, request=None)

        mock_notify.assert_awaited_once_with("delete", 42)


class TestMedicineScheduleAdminHooks:
    async def test_after_model_change_notifies_bot_with_parent_medicine_id(self):
        view = MedicineScheduleAdmin()
        fake_model = MagicMock(medicine_id=7)

        with patch("admin.model_views.notify_bot", AsyncMock()) as mock_notify:
            await view.after_model_change({}, fake_model, is_created=False, request=None)

        mock_notify.assert_awaited_once_with("update", 7)

    async def test_after_model_delete_notifies_bot_with_parent_medicine_id(self):
        view = MedicineScheduleAdmin()
        fake_model = MagicMock(medicine_id=7)

        with patch("admin.model_views.notify_bot", AsyncMock()) as mock_notify:
            await view.after_model_delete(fake_model, request=None)

        mock_notify.assert_awaited_once_with("delete", 7)


class TestSendReminderNowAction:
    async def test_notifies_bot_for_each_selected_medicine(self):
        view = MedicineAdmin()
        request = MagicMock()
        request.query_params.get.return_value = "5,7,9"
        request.headers.get.return_value = "/admin/medicine/list"

        with patch("admin.model_views.notify_bot", AsyncMock()) as mock_notify:
            response = await view.send_reminder_now(request)

        assert mock_notify.await_count == 3
        mock_notify.assert_any_await("send_now", 5)
        mock_notify.assert_any_await("send_now", 7)
        mock_notify.assert_any_await("send_now", 9)
        assert response.status_code == 303

    async def test_redirects_to_the_referer_header(self):
        view = MedicineAdmin()
        request = MagicMock()
        request.query_params.get.return_value = "5"
        request.headers.get.return_value = "/admin/medicine/details/5"

        with patch("admin.model_views.notify_bot", AsyncMock()):
            response = await view.send_reminder_now(request)

        assert response.headers["location"] == "/admin/medicine/details/5"

    async def test_falls_back_to_medicine_list_when_no_referer(self):
        view = MedicineAdmin()
        request = MagicMock()
        request.query_params.get.return_value = "5"
        request.headers.get.return_value = "/admin/medicine/list"  # simulates the .get(..., default) fallback

        with patch("admin.model_views.notify_bot", AsyncMock()):
            response = await view.send_reminder_now(request)

        assert response.headers["location"] == "/admin/medicine/list"

    async def test_ignores_empty_pks_string(self):
        view = MedicineAdmin()
        request = MagicMock()
        request.query_params.get.return_value = ""
        request.headers.get.return_value = "/admin/medicine/list"

        with patch("admin.model_views.notify_bot", AsyncMock()) as mock_notify:
            await view.send_reminder_now(request)

        mock_notify.assert_not_awaited()

    async def test_continues_after_one_medicine_fails(self):
        """A notify_bot failure for one medicine ID shouldn't stop the
        others from being processed."""
        view = MedicineAdmin()
        request = MagicMock()
        request.query_params.get.return_value = "5,7"
        request.headers.get.return_value = "/admin/medicine/list"

        mock_notify = AsyncMock(side_effect=[Exception("bot unreachable"), None])

        with patch("admin.model_views.notify_bot", mock_notify):
            response = await view.send_reminder_now(request)

        assert mock_notify.await_count == 2
        assert response.status_code == 303


class TestChatHistoryContentFormatter:
    def _format(self, content):
        formatter = ChatHistoryAdmin.column_formatters[ChatHistoryAdmin.model.content]
        fake_message = MagicMock(content=content)
        return formatter(fake_message, None)

    def test_leaves_short_content_unchanged(self):
        result = self._format("Take two pills after breakfast")
        assert result == "Take two pills after breakfast"

    def test_truncates_long_content_with_ellipsis(self):
        long_text = "A" * 100
        result = self._format(long_text)
        assert result == "A" * 80 + "…"
        assert len(result) == 81

    def test_handles_none_content(self):
        result = self._format(None)
        assert result is None

    def test_exactly_80_chars_is_not_truncated(self):
        text = "A" * 80
        result = self._format(text)
        assert result == text


class TestChatHistoryAdminPermissions:
    def test_is_read_only_except_export(self):
        assert ChatHistoryAdmin.can_create is False
        assert ChatHistoryAdmin.can_edit is False
        assert ChatHistoryAdmin.can_export is True
