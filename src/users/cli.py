"""Create the owner's account — the only way a SUPER_ADMIN comes to exist.

    uv run python -m src.users.cli create-super-admin \\
        --email owner@example.com --first-name Dale --last-name Nacario

No endpoint can mint a SUPER_ADMIN (spec §6.4), so the first one is made
here, from a machine that holds the database credentials.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from getpass import getpass

from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.security import hash_password
from src.auth.service import insert_user
from src.database.session import SessionLocal, engine
from src.users.domain.roles import RoleCode
from src.users.persistence.models import UserModel
from src.users.persistence.repository import user_repository
from src.utils.errors import AppError

MIN_PASSWORD = 8


async def create_super_admin(
    session: AsyncSession, *, email: str, password: str, first_name: str, last_name: str
) -> UserModel:
    user = await insert_user(
        session,
        UserModel(
            email=email,
            password_hash=hash_password(password),
            first_name=first_name,
            last_name=last_name,
            role_id=await user_repository.role_id(session, RoleCode.SUPER_ADMIN),
            system_admin=True,
            is_active=True,
            email_verified_at=datetime.now(UTC),
        ),
    )
    await session.commit()
    return user


async def _main(args: argparse.Namespace) -> int:
    password = getpass("Password: ")
    if len(password) < MIN_PASSWORD:
        print(f"Password must be at least {MIN_PASSWORD} characters.", file=sys.stderr)
        return 1
    if getpass("Repeat password: ") != password:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    try:
        async with SessionLocal() as session:
            user = await create_super_admin(
                session,
                email=args.email,
                password=password,
                first_name=args.first_name,
                last_name=args.last_name,
            )
    except AppError as exc:
        print(exc.detail, file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(f"Created SUPER_ADMIN {user.email} (id {user.id}).")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m src.users.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-super-admin", help="create the owner's account")
    create.add_argument("--email", required=True)
    create.add_argument("--first-name", required=True)
    create.add_argument("--last-name", required=True)
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
