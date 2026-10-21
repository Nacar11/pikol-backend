import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.users.domain.roles import RoleCode
from src.users.persistence.repository import user_repository
from tests.auth.helpers import make_user, make_venue


async def test_scope_is_mandatory(db_session: AsyncSession) -> None:
    """Forgetting the scope must fail to RUN, not leak quietly (spec §6.2)."""
    with pytest.raises(TypeError):
        await user_repository.list(db_session, page=1, limit=20)  # type: ignore[call-arg]


async def test_scope_filters_by_venue(db_session: AsyncSession) -> None:
    a = await make_venue(db_session, "A")
    b = await make_venue(db_session, "B")
    at_a = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=a.id)
    at_b = await make_user(db_session, role=RoleCode.VENUE_STAFF, venue_id=b.id)

    scoped, _ = await user_repository.list(
        db_session, scope=VenueScope(all_venues=False, venue_id=a.id), page=1, limit=100
    )
    everything, _ = await user_repository.list(
        db_session, scope=VenueScope(all_venues=True, venue_id=None), page=1, limit=100
    )

    assert at_a.id in {u.id for u in scoped}
    assert at_b.id not in {u.id for u in scoped}
    assert {at_a.id, at_b.id} <= {u.id for u in everything}
