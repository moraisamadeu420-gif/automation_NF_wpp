"""
app/workers/scheduler.py
APScheduler jobs:
  - Every Monday at 09:00 (BRT): weekly NFSe prompt
  - Every day at 09:00 (BRT): subscription expiry reminders (3 days before)
"""
import asyncio
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.database.connection import AsyncSessionFactory
from app.integrations.evolution.client import evolution_client
from app.models.credential import NfseCredential
from app.models.session import ConversationState
from app.models.user import User
from app.repositories.session_repository import SessionRepository
from app.utils.period import format_period, previous_week_period


async def weekly_prompt_job() -> None:
    logger.info("Scheduler: running weekly NFSe prompt job")
    start, end = previous_week_period()
    period_str = format_period(start, end)

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(User)
            .join(NfseCredential, NfseCredential.user_id == User.id)
            .where(User.is_active == True, NfseCredential.is_active == True)  # noqa: E712
            .options(selectinload(User.session))
        )
        users = result.scalars().unique().all()

        session_repo = SessionRepository(session)
        notified = 0

        for user in users:
            try:
                # Skip blocked or expired users
                if user.is_blocked:
                    continue
                if user.subscription_expires_at and user.subscription_expires_at < datetime.now():
                    continue

                conv = await session_repo.get_or_create(user.id)
                if conv.state == ConversationState.PROCESSING:
                    logger.info("Skipping {} — already processing", user.whatsapp_number)
                    continue
                ctx = {"periodo": period_str}
                await session_repo.transition(conv, ConversationState.AWAITING_VALUE, ctx)
                await evolution_client.send_text(
                    user.whatsapp_number,
                    f"Informe o valor dos ganhos da semana de {period_str}:",
                )
                notified += 1
            except Exception as exc:
                logger.error("Failed to prompt user {}: {}", user.whatsapp_number, exc)

        await session.commit()

    logger.info("Scheduler: weekly prompt done — {} users notified", notified)


async def cancelled_user_cleanup_job() -> None:
    """Deletes data (credentials, invoices, session) for users who cancelled their
    subscription and whose access expired more than 30 days ago."""
    from sqlalchemy import delete
    cutoff = datetime.now() - timedelta(days=30)

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(User).where(
                User.subscription_cancelled_at.isnot(None),
                User.subscription_expires_at < cutoff,
            )
        )
        users = result.scalars().all()

        removed = 0
        for user in users:
            try:
                await session.delete(user)
                removed += 1
                logger.info(
                    "Cleanup: deleted data for cancelled user {} (expired {})",
                    user.whatsapp_number,
                    user.subscription_expires_at,
                )
            except Exception as exc:
                logger.error("Cleanup failed for user {}: {}", user.whatsapp_number, exc)

        if removed:
            await session.commit()

    logger.info("Scheduler: cleanup job done — {} cancelled users removed", removed)


async def subscription_reminder_job() -> None:
    """Sends a reminder 3 days before trial or subscription expires."""
    logger.info("Scheduler: running subscription expiry reminder job")
    threshold_low = datetime.now() + timedelta(days=2, hours=23)
    threshold_high = datetime.now() + timedelta(days=3, hours=1)

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(User).where(
                User.is_active == True,  # noqa: E712
                User.is_blocked == False,  # noqa: E712
                User.subscription_expires_at.between(threshold_low, threshold_high),
            )
        )
        users = result.scalars().all()

        for user in users:
            try:
                from app.integrations.mercadopago.client import mp_client
                try:
                    url = await asyncio.to_thread(
                        mp_client.create_preference, user.id, user.whatsapp_number
                    )
                except Exception:
                    url = "https://wa.me/5519971721948"

                expires_str = user.subscription_expires_at.strftime("%d/%m/%Y") if user.subscription_expires_at else "?"

                is_trial = (
                    user.subscription_expires_at is not None
                    and user.subscription_expires_at <= user.created_at + timedelta(days=settings.trial_days + 1)
                )
                label = "período gratuito" if is_trial else "assinatura"

                msg = (
                    f"⏰ Seu {label} expira em 3 dias ({expires_str})!\n\n"
                    f"Para continuar usando o bot — R$ {settings.subscription_price:.2f}/mês:\n\n"
                    f"👉 {url}\n\n"
                    "Após o pagamento sua assinatura é renovada automaticamente!"
                )

                await evolution_client.send_text(user.whatsapp_number, msg)
                logger.info("Expiry reminder sent to {}", user.whatsapp_number)
            except Exception as exc:
                logger.error("Failed to send reminder to {}: {}", user.whatsapp_number, exc)

    logger.info("Scheduler: expiry reminder job done — {} users checked", len(users))


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(
        weekly_prompt_job,
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone="America/Sao_Paulo"),
        id="weekly_nfse_prompt",
        replace_existing=True,
    )
    scheduler.add_job(
        subscription_reminder_job,
        CronTrigger(hour=9, minute=5, timezone="America/Sao_Paulo"),
        id="subscription_reminder",
        replace_existing=True,
    )
    scheduler.add_job(
        cancelled_user_cleanup_job,
        CronTrigger(hour=2, minute=0, timezone="America/Sao_Paulo"),
        id="cancelled_user_cleanup",
        replace_existing=True,
    )
    return scheduler
