from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import get_settings

settings = get_settings()

# pool_pre_ping guards against connections killed by an idle pooler — which
# Supabase does. Modest pool size: Supabase's session pooler has a limited
# connection budget, and this process is long-lived (see spec §9.1).
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding one session per request."""
    async with SessionLocal() as session:
        yield session
