from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import require_permission
from src.database.session import get_session
from src.parameters.admin import parameter_admin_service
from src.parameters.dto import ParameterOut, ParameterUpdate
from src.parameters.persistence.repository import ParameterRepository
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX

router = APIRouter(prefix=f"{API_PREFIX}/admin/parameters", tags=["admin: parameters"])

manage_parameters = require_permission("PARAMETER:Manage")


@router.get("")
async def list_parameters(
    _: UserModel = Depends(manage_parameters),
    session: AsyncSession = Depends(get_session),
) -> list[ParameterOut]:
    rows = await ParameterRepository().list_rows(session)
    return [ParameterOut.of(r) for r in rows]


@router.patch("/{key}")
async def update_parameter(
    key: str,
    body: ParameterUpdate,
    caller: UserModel = Depends(manage_parameters),
    session: AsyncSession = Depends(get_session),
) -> ParameterOut:
    return ParameterOut.of(await parameter_admin_service.update(session, caller, key, body.value))
