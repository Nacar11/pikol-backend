from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import require_customer
from src.auth.scope import VenueScope
from src.database.session import get_session
from src.favorites.persistence.repository import favorite_repository
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.venues.dto import VenueOut
from src.venues.service import venue_service

# For the mobile app's Favorites tab; the web has no favorites UI.
router = APIRouter(prefix=f"{API_PREFIX}/users/me/favorite-venues", tags=["favorites"])

CATALOG = VenueScope.everything()


@router.get("")
async def my_favorites(
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> list[VenueOut]:
    """Active venues only, newest favorite first."""
    venues = await favorite_repository.list_active(session, customer.id)
    return [VenueOut.of(v) for v in venues]


@router.put("/{venue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_favorite(
    venue_id: int,
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await venue_service.get(session, venue_id, scope=CATALOG, active_only=True)  # 404
    await favorite_repository.add(session, customer.id, venue_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{venue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    venue_id: int,
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await favorite_repository.remove(session, customer.id, venue_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
