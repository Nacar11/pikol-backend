import json
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.database.session import get_session
from src.email.port import EmailSender, get_email_sender
from src.payments.domain.port import PaymentProvider, ProviderError
from src.payments.provider import get_payment_provider
from src.payments.service import payment_service
from src.utils.api import API_PREFIX
from src.utils.errors import AppError
from src.utils.net import client_ip

logger = logging.getLogger("pikol.payments")

# Three exemptions, each easy to miss (spec §7.6): no JWT — Maya has none;
# no rate limit — Maya gives up after four attempts; raw body kept — the
# stored payload is the bytes Maya sent.
router = APIRouter(prefix=f"{API_PREFIX}/webhooks", tags=["webhooks"])


@router.post("/maya")
async def maya_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    provider: PaymentProvider = Depends(get_payment_provider),
    sender: EmailSender = Depends(get_email_sender),
) -> dict[str, str]:
    """A NOTIFICATION, never evidence. Maya does not sign webhooks, so only
    the payment id is read from the body; everything acted on comes from
    asking Maya directly (ADR 0002 §1).

    2xx once handled (or deliberately ignored); 502 only when the provider
    cannot be reached, so Maya retries.
    """
    settings = get_settings()
    if settings.maya_webhook_allowed_ips:
        source = client_ip(request, settings.trusted_proxy_hops)
        if source not in settings.maya_webhook_allowed_ips:
            logger.warning("webhook from a non-allowlisted address rejected")
            raise AppError(403, "FORBIDDEN", "Not an allowed webhook source.")

    body = await request.body()
    try:
        notice = provider.parse_webhook(body)
    except ProviderError:
        raise AppError(400, "BAD_WEBHOOK", "Unreadable webhook body.") from None

    await payment_service.sync_payment(
        session, provider, notice.payment_id, sender, event_payload=json.loads(body)
    )
    return {"status": "ok"}
