"""CRUD operations for AI usage metrics (latency, tool usage, error rates)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AIMetric, User


# ─── AI metrics ─────────────────────────────────────────────────────────────
async def log_ai_metric(
    session: AsyncSession,
    user_id: int,
    model_used: str,
    tool_choice: str | None,
    tool_names: list[str] | None,
    latency_ms: int,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    session.add(
        AIMetric(
            user_id=user_id,
            model_used=model_used,
            tool_choice=tool_choice,
            tool_names=",".join(tool_names) if tool_names else None,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
        )
    )
    await session.flush()


async def get_ai_metrics_summary(session: AsyncSession, period: str = "24h") -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    period_map = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    date_filter = now - period_map[period] if period in period_map else None

    def _apply_filter(stmt):
        return stmt.where(AIMetric.created_at >= date_filter) if date_filter else stmt

    total = (await session.execute(_apply_filter(select(func.count(AIMetric.id))))).scalar_one()

    avg_latency = (await session.execute(_apply_filter(select(func.avg(AIMetric.latency_ms))))).scalar_one() or 0

    by_status_result = await session.execute(
        _apply_filter(select(AIMetric.status, func.count(AIMetric.id)).group_by(AIMetric.status))
    )

    return {
        "total_calls": total,
        "avg_latency_ms": round(float(avg_latency), 1),
        "by_status": {str(s): int(c) for s, c in by_status_result.all()},
    }


async def get_recent_ai_metrics(session: AsyncSession, limit: int = 50) -> list[tuple]:
    stmt = (
        select(AIMetric, User.full_name)
        .join(User, AIMetric.user_id == User.id)
        .order_by(AIMetric.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]
