"""Demo customers for the web booking flow:
`uv run python -m src.devtools.demo_seed --customers 3`.

Creates `customer1..N@example.com`, verified and in the Customer group, all
with one shared development password. Idempotent: an existing account is
left alone except to be added to the Customer group. Refuses the e2e
database and anything that looks like production.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import hash_password
from src.auth.service import insert_user
from src.config.settings import get_settings
from src.database.session import SessionLocal, engine
from src.users.persistence.group_repository import group_repository
from src.users.persistence.models import UserModel

PASSWORD = "Demo-password-1"


def assert_demo_database(url: str) -> None:
    parsed = urlparse(url)
    name = parsed.path.lstrip("/")
    if name.endswith("_e2e") or "supabase" in (parsed.hostname or ""):
        print(f"Refusing to seed {name!r} on {parsed.hostname!r}: local development only.")
        raise SystemExit(2)


async def seed_customers(
    session: AsyncSession, emails: Sequence[str], *, password: str
) -> list[str]:
    """Make each email a verified customer. Returns the emails."""
    customers = await group_repository.customer_group_id(session)
    for email in emails:
        user = await session.scalar(
            select(UserModel).where(
                func.lower(UserModel.email) == email.lower(), UserModel.deleted_at.is_(None)
            )
        )
        if user is None:
            user = await insert_user(
                session,
                UserModel(
                    email=email,
                    password_hash=hash_password(password),
                    first_name="Demo",
                    last_name=email.split("@")[0].capitalize(),
                    is_active=True,
                    email_verified_at=datetime.now(UTC),
                ),
            )
        if not await group_repository.is_customer(session, user.id):
            await group_repository.add_member(session, customers, user.id, added_by=None)
    await session.flush()
    return list(emails)


async def _main(url: str, count: int) -> list[str]:
    assert_demo_database(url)
    try:
        async with SessionLocal() as session:
            emails = await seed_customers(
                session,
                [f"customer{n}@example.com" for n in range(1, count + 1)],
                password=PASSWORD,
            )
            await session.commit()
            return emails
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed demo customer accounts.")
    parser.add_argument("--customers", type=int, default=3)
    args = parser.parse_args()
    seeded = asyncio.run(_main(get_settings().database_url, args.customers))
    print("Demo customers (password " + PASSWORD + "):")
    for email in seeded:
        print(f"  {email}")
    sys.exit(0)
