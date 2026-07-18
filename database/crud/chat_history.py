"""CRUD operations for the AI conversation history (sliding window)."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ChatHistory


# ─── AI conversation history ───────────────────────────────────────────────
async def add_chat_message(
    session: AsyncSession,
    user_id: int,
    role: str,
    content: str,
    keep_last: int = 20,
) -> None:
    session.add(ChatHistory(user_id=user_id, role=role, content=content))
    await session.flush()

    subq = (
        select(ChatHistory.id)
        .where(ChatHistory.user_id == user_id)
        .order_by(ChatHistory.created_at.desc(), ChatHistory.id.desc())
        .limit(keep_last)
    )
    result = await session.execute(subq)
    keep_ids = [row[0] for row in result.all()]

    if keep_ids:
        await session.execute(
            delete(ChatHistory).where(
                ChatHistory.user_id == user_id,
                ChatHistory.id.notin_(keep_ids),
            )
        )
        await session.flush()


async def get_chat_history(session: AsyncSession, user_id: int, limit: int = 10) -> list[dict]:
    result = await session.execute(
        select(ChatHistory)
        .where(ChatHistory.user_id == user_id)
        .order_by(ChatHistory.created_at.desc(), ChatHistory.id.desc())
        .limit(limit)
    )
    messages = list(result.scalars().all())
    messages.reverse()
    return [{"role": str(m.role), "content": str(m.content)} for m in messages]


async def clear_chat_history(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(ChatHistory).where(ChatHistory.user_id == user_id))
    await session.flush()
