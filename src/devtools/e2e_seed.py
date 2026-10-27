"""Seed the Playwright database: `uv run python -m src.devtools.e2e_seed`.

Goes through the real services so seeded rows meet the same invariants as
real ones. Refuses any database whose name does not end in `_e2e`.
"""

import asyncio
import sys
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.auth.security import hash_password
from src.auth.service import insert_user
from src.config.settings import get_settings
from src.courts.dto import CourtCreate
from src.courts.service import court_service
from src.database.session import SessionLocal, engine
from src.devtools.demo_seed import seed_customers
from src.users.cli import create_super_admin
from src.users.dto import MemberAdd
from src.users.group_service import group_service
from src.users.persistence.models import UserGroupModel, UserModel
from src.venues.dto import VenueCreate
from src.venues.service import venue_service

PASSWORD = "E2e-password-1"
OWNER_EMAIL = "owner.e2e@example.com"
DESK_EMAIL = "desk.e2e@example.com"
CUSTOMER_EMAILS = ("customer1.e2e@example.com", "customer2.e2e@example.com")
VENUES = {"E2E Riverside": 3, "E2E Lakeside": 1}


def assert_e2e_database(url: str) -> None:
    name = urlparse(url).path.lstrip("/")
    if not name.endswith("_e2e"):
        print(f"Refusing to seed {name!r}: the e2e seed only runs on a *_e2e database.")
        raise SystemExit(2)


async def seed(session: AsyncSession) -> None:
    everywhere = VenueScope.everything()
    owner = await create_super_admin(
        session, email=OWNER_EMAIL, password=PASSWORD, first_name="Olive", last_name="Owner"
    )
    venue_ids: dict[str, int] = {}
    for name, courts in VENUES.items():
        venue = await venue_service.create(session, owner, VenueCreate(name=name))
        venue_ids[name] = venue.id
        for n in range(1, courts + 1):
            await court_service.create(
                session,
                owner,
                venue.id,
                CourtCreate(name=f"Court {n}", sort_order=n),
                scope=everywhere,
            )
    await insert_user(
        session,
        UserModel(
            email=DESK_EMAIL,
            password_hash=hash_password(PASSWORD),
            first_name="Dana",
            last_name="Desk",
            is_active=True,
            email_verified_at=datetime.now(UTC),
        ),
    )
    front_desk = await session.scalar(
        select(UserGroupModel.id).where(
            UserGroupModel.venue_id == venue_ids["E2E Riverside"],
            UserGroupModel.name == "Front desk",
        )
    )
    assert front_desk is not None
    await group_service.add_member(
        session, owner, front_desk, MemberAdd(email=DESK_EMAIL), scope=everywhere
    )
    await seed_customers(session, CUSTOMER_EMAILS, password=PASSWORD)
    await session.commit()


async def _main(url: str) -> None:
    assert_e2e_database(url)
    try:
        async with SessionLocal() as session:
            await seed(session)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main(get_settings().database_url))
    print(f"Seeded {OWNER_EMAIL}, {DESK_EMAIL} and customers (password {PASSWORD}).")
    sys.exit(0)
