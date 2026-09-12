from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.session import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness. Deliberately touches nothing.

    A database outage must not make the orchestrator kill a container that is
    otherwise healthy and serving cached reads.
    """
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Readiness. Issues a real query.

    This is the endpoint the keep-alive monitor must hit: it is what keeps
    Supabase from pausing the project after 7 idle days (spec §9.3).
    """
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}
