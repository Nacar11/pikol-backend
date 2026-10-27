from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import require_customer
from src.checkouts.dto import CheckoutCreate, CheckoutOut
from src.checkouts.persistence.repository import checkout_repository
from src.checkouts.service import checkout_service
from src.database.session import get_session
from src.users.persistence.models import UserModel
from src.utils.api import API_PREFIX
from src.utils.pagination import Page

# Identity from the token, never the URL: /checkouts/me, not /checkouts/{customer}.
# The same routes serve the web demo and the mobile app.
router = APIRouter(prefix=f"{API_PREFIX}/checkouts/me", tags=["checkouts"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_checkout(
    body: CheckoutCreate,
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> CheckoutOut:
    """Pay now: hold every picked hour at one venue, all or nothing."""
    checkout = await checkout_service.create(session, customer, body)
    return await checkout_service.out(session, checkout)


@router.get("")
async def my_checkouts(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> Page[CheckoutOut]:
    checkouts, total = await checkout_repository.list_for_customer(
        session, customer.id, page=page, limit=limit
    )
    data = [await checkout_service.out(session, c) for c in checkouts]
    return Page.of(data, total=total, page=page, limit=limit)


@router.get("/{checkout_id}")
async def my_checkout(
    checkout_id: int,
    customer: UserModel = Depends(require_customer),
    session: AsyncSession = Depends(get_session),
) -> CheckoutOut:
    """The hold page and the result page read this."""
    checkout = await checkout_service.get_mine(session, customer, checkout_id)
    return await checkout_service.out(session, checkout)
