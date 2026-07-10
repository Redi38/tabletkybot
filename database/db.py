from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from database.models import Base
def _create_tables(sync_conn: Connection) -> None:
    """Helper synchronous function for proper typing."""
    Base.metadata.create_all(sync_conn)
async def init_db(database_url: str) -> async_sessionmaker:
    """Initializes the database connection and creates the tables.
    Returns the session_factory for use in middleware."""
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
    # Automatically create the tables on first run
    async with engine.begin() as conn:
        await conn.run_sync(_create_tables)
    return session_factory
