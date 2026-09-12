from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.session import engine, get_session
from src.main import create_app


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests() -> AsyncGenerator[None, None]:
    """pytest-asyncio gives every test its own event loop.

    The module-level engine pools connections, so one checked out under test
    A's loop is handed back and reused under test B's — and asyncpg raises
    `RuntimeError: Future attached to a different loop`, or `Event loop is
    closed`. It looks like flakiness and it is not: it is deterministic once
    two tests touch the database. Disposing after each test means no
    connection outlives the loop that created it.
    """
    yield
    await engine.dispose()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A session inside a transaction that is always rolled back.

    The test sees its own writes; the database never does.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """The app, with its session dependency pointed at the rolling-back one.

    Without this override the app opens its OWN session on the real engine
    and commits for real — so the rollback fixture would guarantee nothing
    about anything a request writes, which is most of what Phase 2 onward
    tests. The fixture would be decorative.
    """
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
