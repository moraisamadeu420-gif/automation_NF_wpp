"""
app/workers/scheduler.py
APScheduler job: every Monday at 09:00 (America/Sao_Paulo) sends the weekly
NFSe prompt to all active, configured users.
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

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


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(
        weekly_prompt_job,
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone="America/Sao_Paulo"),
        id="weekly_nfse_prompt",
        replace_existing=True,
    )
    return scheduler
