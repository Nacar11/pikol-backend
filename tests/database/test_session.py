from sqlalchemy import text

from src.database.session import get_session


async def test_session_connects_to_postgres() -> None:
    agen = get_session()
    session = await anext(agen)
    try:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
    finally:
        await agen.aclose()
