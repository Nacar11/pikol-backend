from typing import Any

from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from src.parameters.service import parameter_service
from src.utils.api import API_PREFIX
from tests.auth.helpers import RoleCode, bearer, login, make_user, make_venue

URL = f"{API_PREFIX}/admin/parameters"


async def _admin(db_session: AsyncSession, client: AsyncClient) -> dict[str, str]:
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    return bearer(await login(client, admin.email))


async def test_super_admin_lists_every_parameter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.get(URL, headers=await _admin(db_session, client))
    assert response.status_code == 200
    rows = response.json()
    assert "hold_duration_minutes" in {r["key"] for r in rows}
    assert [r["key"] for r in rows] == sorted(r["key"] for r in rows)
    assert set(rows[0]) == {
        "key",
        "value",
        "value_type",
        "description",
        "updated_at",
        "updated_by_user_id",
    }


async def test_manager_and_staff_get_403(client: AsyncClient, db_session: AsyncSession) -> None:
    venue = await make_venue(db_session)
    for role in (RoleCode.VENUE_MANAGER, RoleCode.VENUE_STAFF):
        user = await make_user(db_session, role=role, venue_id=venue.id)
        headers = bearer(await login(client, user.email))
        assert (await client.get(URL, headers=headers)).status_code == 403
        patch = await client.patch(
            f"{URL}/hold_duration_minutes", json={"value": "20"}, headers=headers
        )
        assert patch.status_code == 403


async def test_patch_updates_value_and_audit(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await make_user(db_session, role=RoleCode.SUPER_ADMIN, system_admin=True)
    headers = bearer(await login(client, admin.email))
    response = await client.patch(
        f"{URL}/hold_duration_minutes", json={"value": "10"}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["value"] == "10"
    assert body["updated_by_user_id"] == admin.id
    assert (await parameter_service.get(db_session)).hold_duration_minutes == 10


async def test_owner_sets_min_lead_to_zero(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = await _admin(db_session, client)
    response = await client.patch(f"{URL}/min_lead_minutes", json={"value": "0"}, headers=headers)
    assert response.status_code == 200, response.text
    assert (await parameter_service.get(db_session)).min_lead_minutes == 0


async def test_patch_rejects_bad_type(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = await _admin(db_session, client)
    response = await client.patch(
        f"{URL}/hold_duration_minutes", json={"value": "abc"}, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_PARAMETER"


async def test_unknown_key_is_404(client: AsyncClient, db_session: AsyncSession) -> None:
    response = await client.patch(
        f"{URL}/nope", json={"value": "1"}, headers=await _admin(db_session, client)
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_patch_body_forbids_extra_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.patch(
        f"{URL}/hold_duration_minutes",
        json={"value": "10", "key": "x"},
        headers=await _admin(db_session, client),
    )
    assert response.status_code == 422


async def test_update_locks_every_parameter_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A true two-session race cannot run inside the rolling-back db_session,
    so assert the update path issues SELECT ... FOR UPDATE on the table."""
    headers = await _admin(db_session, client)
    statements: list[str] = []

    def capture(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = db_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = await client.patch(
            f"{URL}/hold_duration_minutes", json={"value": "10"}, headers=headers
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert any("FROM parameters" in s and "FOR UPDATE" in s for s in statements)
