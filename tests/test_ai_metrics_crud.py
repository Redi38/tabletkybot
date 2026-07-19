"""
Tests for database/crud/ai_metrics.py against a real (in-memory SQLite)
async session. See the `db_session` fixture in conftest.py.
"""

from datetime import datetime, timedelta, timezone

import database.crud as crud
from database.models import AIMetric


class TestLogAIMetric:
    async def test_creates_a_metric_row(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")

        await crud.log_ai_metric(
            db_session,
            user_id=user.id,
            model_used="claude-sonnet-4-6",
            tool_choice="auto",
            tool_names=["add_medicine", "list_medicines"],
            latency_ms=842,
        )
        await db_session.commit()

        summary = await crud.get_ai_metrics_summary(db_session, period="24h")
        assert summary["total_calls"] == 1
        assert summary["avg_latency_ms"] == 842.0

    async def test_joins_tool_names_with_comma(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")

        await crud.log_ai_metric(
            db_session,
            user_id=user.id,
            model_used="claude-sonnet-4-6",
            tool_choice="auto",
            tool_names=["add_medicine", "list_medicines", "archive_medicine"],
            latency_ms=100,
        )
        await db_session.commit()

        recent = await crud.get_recent_ai_metrics(db_session, limit=1)
        metric, _full_name = recent[0]
        assert metric.tool_names == "add_medicine,list_medicines,archive_medicine"

    async def test_none_tool_names_stored_as_none(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")

        await crud.log_ai_metric(
            db_session,
            user_id=user.id,
            model_used="claude-sonnet-4-6",
            tool_choice=None,
            tool_names=None,
            latency_ms=50,
        )
        await db_session.commit()

        recent = await crud.get_recent_ai_metrics(db_session, limit=1)
        metric, _full_name = recent[0]
        assert metric.tool_names is None

    async def test_defaults_status_to_success(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")

        await crud.log_ai_metric(
            db_session, user_id=user.id, model_used="m", tool_choice=None, tool_names=None, latency_ms=1
        )
        await db_session.commit()

        recent = await crud.get_recent_ai_metrics(db_session, limit=1)
        metric, _ = recent[0]
        assert metric.status == "success"

    async def test_records_error_status_and_message(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")

        await crud.log_ai_metric(
            db_session,
            user_id=user.id,
            model_used="m",
            tool_choice=None,
            tool_names=None,
            latency_ms=5000,
            status="error",
            error_message="Timed out waiting for NVIDIA API",
        )
        await db_session.commit()

        recent = await crud.get_recent_ai_metrics(db_session, limit=1)
        metric, _ = recent[0]
        assert metric.status == "error"
        assert metric.error_message == "Timed out waiting for NVIDIA API"


class TestGetAIMetricsSummary:
    async def test_empty_summary_when_no_metrics(self, db_session):
        summary = await crud.get_ai_metrics_summary(db_session, period="24h")

        assert summary["total_calls"] == 0
        assert summary["avg_latency_ms"] == 0.0
        assert summary["by_status"] == {}

    async def test_averages_latency_across_calls(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")
        for latency in (100, 200, 300):
            await crud.log_ai_metric(
                db_session, user_id=user.id, model_used="m", tool_choice=None, tool_names=None, latency_ms=latency
            )
        await db_session.commit()

        summary = await crud.get_ai_metrics_summary(db_session, period="24h")

        assert summary["total_calls"] == 3
        assert summary["avg_latency_ms"] == 200.0

    async def test_groups_by_status(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")
        await crud.log_ai_metric(
            db_session,
            user_id=user.id,
            model_used="m",
            tool_choice=None,
            tool_names=None,
            latency_ms=1,
            status="success",
        )
        await crud.log_ai_metric(
            db_session,
            user_id=user.id,
            model_used="m",
            tool_choice=None,
            tool_names=None,
            latency_ms=1,
            status="success",
        )
        await crud.log_ai_metric(
            db_session, user_id=user.id, model_used="m", tool_choice=None, tool_names=None, latency_ms=1, status="error"
        )
        await db_session.commit()

        summary = await crud.get_ai_metrics_summary(db_session, period="24h")

        assert summary["by_status"] == {"success": 2, "error": 1}

    async def test_excludes_metrics_older_than_the_period(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")

        old_metric = AIMetric(
            user_id=user.id,
            model_used="m",
            tool_choice=None,
            tool_names=None,
            latency_ms=999,
            status="success",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2),
        )
        db_session.add(old_metric)
        await db_session.commit()

        summary = await crud.get_ai_metrics_summary(db_session, period="24h")

        assert summary["total_calls"] == 0

    async def test_unknown_period_returns_all_time(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")

        old_metric = AIMetric(
            user_id=user.id,
            model_used="m",
            tool_choice=None,
            tool_names=None,
            latency_ms=999,
            status="success",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365),
        )
        db_session.add(old_metric)
        await db_session.commit()

        summary = await crud.get_ai_metrics_summary(db_session, period="not-a-real-period")

        assert summary["total_calls"] == 1


class TestGetRecentAIMetrics:
    async def test_returns_most_recent_first(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")

        db_session.add(
            AIMetric(
                user_id=user.id,
                model_used="older",
                tool_choice=None,
                tool_names=None,
                latency_ms=1,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2),
            )
        )
        db_session.add(
            AIMetric(
                user_id=user.id,
                model_used="newer",
                tool_choice=None,
                tool_names=None,
                latency_ms=1,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await db_session.commit()

        recent = await crud.get_recent_ai_metrics(db_session, limit=10)

        assert recent[0][0].model_used == "newer"
        assert recent[1][0].model_used == "older"

    async def test_respects_limit(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi")
        for i in range(5):
            await crud.log_ai_metric(
                db_session, user_id=user.id, model_used=f"m{i}", tool_choice=None, tool_names=None, latency_ms=1
            )
        await db_session.commit()

        recent = await crud.get_recent_ai_metrics(db_session, limit=2)

        assert len(recent) == 2

    async def test_includes_user_full_name_via_join(self, db_session):
        user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Shershnov")
        await crud.log_ai_metric(
            db_session, user_id=user.id, model_used="m", tool_choice=None, tool_names=None, latency_ms=1
        )
        await db_session.commit()

        recent = await crud.get_recent_ai_metrics(db_session, limit=1)

        _metric, full_name = recent[0]
        assert full_name == "Redi Shershnov"
