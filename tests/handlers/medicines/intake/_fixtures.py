from unittest.mock import AsyncMock, MagicMock

from database import crud


def _fake_state():
    state = MagicMock()
    state.update_data = AsyncMock()
    return state


async def _add_medicine(
    session, user_id=1, stock_amount=None, low_stock_threshold=5, course_duration=10, schedules_list=("09:00",)
):
    await crud.get_or_create_user(session, user_id, "tester", "Test User")
    medicine = await crud.add_medicine(
        session,
        user_id=user_id,
        name="Ibuprofen",
        form="tablets",
        dosage="200mg",
        schedules_list=list(schedules_list),
        course_duration=course_duration,
        stock_amount=stock_amount,
        low_stock_threshold=low_stock_threshold,
    )
    await session.commit()
    return medicine
