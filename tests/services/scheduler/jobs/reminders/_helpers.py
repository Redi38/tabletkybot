"""
Shared test helpers for the modules in tests/services/scheduler/jobs/reminders/
"""


class _FakeSessionFactory:
    """
    Minimal stand-in for `async_sessionmaker` that hands back the same
    already-open test `db_session` via `async with session_factory() as
    session`, instead of opening a brand-new engine/connection.

    NOTE: unlike a real `async_sessionmaker`-produced session, exiting this
    context manager does NOT close/rollback the session — it's the same
    `db_session` reused across the whole test. That means a caller that
    flushes a write but forgets `await session.commit()` will NOT be caught
    by this fake (the change stays visible via the shared identity map, even
    though a real session.close() would roll it back). This bit us once
    already — see the missing-commit bug in _archive_for_inactivity — so
    any code path that writes through session_factory() must call
    session.commit() explicitly; don't rely on this fake to catch a missing
    one.
    """

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False
