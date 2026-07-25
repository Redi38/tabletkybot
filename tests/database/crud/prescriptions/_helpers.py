import database.crud as crud


async def _make_user(db_session, user_id: int = 1) -> None:
    await crud.get_or_create_user(db_session, user_id, "redi", "Redi Test")
