"""
app/api/routes/webhook.py
Receives Evolution API webhook events and dispatches to MessageProcessor.
"""
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from loguru import logger

from app.api.dependencies import require_webhook_auth
from app.database.connection import AsyncSessionFactory
from app.integrations.evolution.schemas import WebhookEvent
from app.schemas.webhook import WebhookResponse
from app.services.message_processor import MessageProcessor

router = APIRouter(prefix="/webhook", tags=["webhook"])

# Evolution API event names that carry WhatsApp messages
_MESSAGE_EVENTS = {"messages.upsert", "MESSAGES_UPSERT", "message"}


async def _process_event(raw: dict[str, Any]) -> None:
    """
    Runs in background — creates its own DB session.
    Never reuse a session from a FastAPI dependency here; it is already
    closed by the time the background task executes.
    """
    event_name = raw.get("event", "")
    instance = raw.get("instance", "")
    data = raw.get("data", {})

    logger.debug("Background task — event: '{}' | instance: '{}'", event_name, instance)

    if event_name not in _MESSAGE_EVENTS:
        logger.debug("Event '{}' ignored (not a message event)", event_name)
        return

    if not isinstance(data, (dict, list)):
        logger.warning("Unexpected 'data' type: {} — payload: {}", type(data).__name__, str(raw)[:300])
        return

    try:
        event = WebhookEvent(event=event_name, instance=instance, data=data)
        messages = event.extract_messages()
    except Exception as exc:
        logger.warning("Failed to build WebhookEvent — {}\ndata dump: {}", exc, str(data)[:500])
        return

    if not messages:
        logger.warning("No parseable messages in payload — data: {}", str(data)[:500])
        return

    logger.info("Dispatching {} message(s) to processor", len(messages))

    async with AsyncSessionFactory() as session:
        processor = MessageProcessor(session)
        for message in messages:
            try:
                await processor.process(message)
            except Exception as exc:
                logger.exception("Unhandled error processing message {}: {}", message.key.id, exc)
        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.exception("Session commit failed: {}", exc)


@router.post(
    "",
    response_model=WebhookResponse,
    dependencies=[Depends(require_webhook_auth)],
    summary="Evolution API webhook receiver",
)
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> WebhookResponse:
    # Read raw body first — Pydantic validation comes after so we can log
    # exactly what Evolution API sent even when parsing fails.
    body_bytes = await request.body()

    try:
        raw: dict[str, Any] = json.loads(body_bytes)
    except json.JSONDecodeError:
        logger.warning("Webhook received non-JSON body: {}", body_bytes[:200])
        return WebhookResponse()

    event_name = raw.get("event", "<no-event>")
    instance = raw.get("instance", "<no-instance>")
    logger.info("Webhook received — event: '{}' | instance: '{}'", event_name, instance)
    logger.debug("Webhook raw payload: {}", str(raw)[:1000])

    background_tasks.add_task(_process_event, raw)
    return WebhookResponse()


@router.post(
    "/debug",
    include_in_schema=False,
)
async def webhook_debug(request: Request) -> dict:
    body_bytes = await request.body()
    headers = dict(request.headers)
    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError:
        body = body_bytes.decode(errors="replace")

    logger.info("DEBUG webhook — headers: {}", {k: v for k, v in headers.items() if "key" not in k.lower()})
    logger.info("DEBUG webhook — body: {}", str(body)[:2000])
    return {"received": True, "event": body.get("event") if isinstance(body, dict) else None}


@router.post(
    "/trigger-weekly-prompt",
    include_in_schema=False,
)
async def trigger_weekly_prompt(background_tasks: BackgroundTasks) -> dict:
    """Dev/test endpoint — runs the Monday scheduler job immediately."""
    from app.workers.scheduler import weekly_prompt_job
    background_tasks.add_task(weekly_prompt_job)
    logger.info("Manual trigger: weekly_prompt_job queued")
    return {"status": "queued", "job": "weekly_prompt_job"}
