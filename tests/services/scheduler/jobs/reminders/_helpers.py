"""
Shared test helpers for the modules in tests/services/scheduler/jobs/reminders/
"""


class _FakeSessionFactory:
    """
    Minimal stand-in for `async_sessionmaker` that hands back the same
    already-open test `db_session` via `async with session_factory() as
    session`, instead of opening a brand-new engine/connection.
    """

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False
