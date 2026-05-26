"""
app/api/routes/mercadopago.py
Receives Mercado Pago payment notifications and activates subscriptions.
"""
import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, Request
from loguru import logger

from app.core.config import settings
from app.database.connection import AsyncSessionFactory
from app.integrations.evolution.client import evolution_client
from app.repositories.user_repository import UserRepository
from app.services.user_service import UserService

router = APIRouter(prefix="/webhook/mercadopago", tags=["mercadopago"])

# Previne race condition quando o MP envia o mesmo webhook duas vezes em paralelo
_PROCESSING: set[str] = set()


def _verify_signature(body: bytes, x_signature: str | None, x_request_id: str | None, data_id: str | None) -> bool:
    """Verifies Mercado Pago HMAC-SHA256 signature. Returns True if valid or if no secret configured."""
    if settings.mercadopago_skip_signature:
        return True

    secret = settings.mercadopago_webhook_secret
    if not secret or not x_signature:
        return True

    ts = ""
    v1 = ""
    for part in x_signature.split(","):
        if part.startswith("ts="):
            ts = part[3:]
        elif part.startswith("v1="):
            v1 = part[3:]

    signed_parts = []
    if data_id:
        signed_parts.append(f"id:{data_id}")
    if x_request_id:
        signed_parts.append(f"request-id:{x_request_id}")
    if ts:
        signed_parts.append(f"ts:{ts}")
    manifest = ";".join(signed_parts) + ";"

    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


async def _handle_payment(payment_id: str) -> None:
    if payment_id in _PROCESSING:
        logger.debug("Payment {} já está sendo processado, ignorando duplicata", payment_id)
        return
    _PROCESSING.add(payment_id)
    try:
        await _process_payment(payment_id)
    finally:
        _PROCESSING.discard(payment_id)


async def _notify_affiliate(affiliate_code: str, whatsapp_number: str) -> None:
    import httpx
    url = settings.affiliate_api_url
    key = settings.affiliate_api_key
    if not url or not key:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{url.rstrip('/')}/api/conversion",
                data={"affiliate_code": affiliate_code, "whatsapp_number": whatsapp_number},
                headers={"X-Api-Key": key},
                timeout=10,
            )
        logger.info("Affiliate conversion registered for code '{}'", affiliate_code)
    except Exception as exc:
        logger.warning("Failed to notify affiliate system for code '{}': {}", affiliate_code, exc)


async def _process_payment(payment_id: str) -> None:
    import asyncio
    from app.integrations.mercadopago.client import mp_client

    try:
        payment = await asyncio.to_thread(mp_client.get_payment, payment_id)
    except Exception as exc:
        logger.error("MP payment.get failed for id {}: {}", payment_id, exc)
        return

    status = payment.get("status", "")
    logger.info("MP payment {} — status: {}", payment_id, status)

    if status != "approved":
        logger.info("Payment {} not approved (status={}), skipping", payment_id, status)
        return

    user_id_str = str(payment.get("external_reference", ""))
    if not user_id_str.isdigit():
        logger.warning("MP payment {} has no valid external_reference: {}", payment_id, user_id_str)
        return

    user_id = int(user_id_str)
    whatsapp_number = payment.get("metadata", {}).get("whatsapp_number", "")

    async with AsyncSessionFactory() as session:
        repo = UserRepository(session)
        user = await repo.get_by_id(user_id)
        if not user:
            logger.warning("MP payment {}: user_id {} not found", payment_id, user_id)
            return

        if user.last_payment_id == payment_id:
            logger.info("MP payment {} already processed for user {}, skipping duplicate", payment_id, user_id)
            return

        svc = UserService(session)
        await svc.activate_subscription(user, settings.subscription_days)
        user.subscription_cancelled_at = None
        user.last_payment_id = payment_id
        await session.commit()

        affiliate_code = payment.get("metadata", {}).get("affiliate_code", "")
        if affiliate_code:
            await _notify_affiliate(affiliate_code, whatsapp_number or user.whatsapp_number)

        expires = user.subscription_expires_at
        expires_str = expires.strftime("%d/%m/%Y") if expires else "?"
        number = whatsapp_number or user.whatsapp_number
        if number:
            await evolution_client.send_text(
                number,
                f"✅ Pagamento confirmado!\n"
                f"Sua assinatura está ativa até {expires_str}.\n\n"
                "Digite 1 para emitir sua NFS-e agora!",
            )

    logger.info("Subscription activated for user {} via payment {}", user_id, payment_id)


@router.post("")
async def mercadopago_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature: str | None = Header(None),
    x_request_id: str | None = Header(None),
) -> dict[str, Any]:
    body_bytes = await request.body()

    try:
        body: dict[str, Any] = json.loads(body_bytes)
    except json.JSONDecodeError:
        logger.warning("MP webhook: non-JSON body received")
        return {"status": "ignored"}

    logger.info("MP webhook received: {}", str(body)[:300])

    data_id: str | None = None
    if isinstance(body.get("data"), dict):
        data_id = str(body["data"].get("id", ""))
    if not data_id:
        data_id = request.query_params.get("id")

    if not _verify_signature(body_bytes, x_signature, x_request_id, data_id):
        logger.warning("MP webhook: invalid signature — ignoring")
        return {"status": "ignored"}

    topic = body.get("type") or body.get("topic") or request.query_params.get("topic", "")

    if topic not in ("payment", ""):
        logger.debug("MP webhook: topic '{}' ignored", topic)
        return {"status": "ignored"}

    if not data_id:
        logger.debug("MP webhook: no payment id found in notification")
        return {"status": "ignored"}

    background_tasks.add_task(_handle_payment, data_id)
    return {"status": "queued", "payment_id": data_id}
