from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from database.models import Base


def _create_tables(sync_conn: Connection) -> None:
    """Допоміжна синхронна функція для ідеальної типізації."""
    Base.metadata.create_all(sync_conn)


async def init_db(database_url: str) -> async_sessionmaker:
    """Ініціалізація підключення до бази даних та створення таблиць.
    Повертає session_factory для використання у middleware."""

    engine = create_async_engine(
        database_url,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
    )

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Автоматичне створення таблиць при першому запуску
    async with engine.begin() as conn:
        await conn.run_sync(_create_tables)

    return session_factory