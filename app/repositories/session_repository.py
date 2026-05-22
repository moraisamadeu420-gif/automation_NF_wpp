"""
app/repositories/session_repository.py
"""
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import ConversationState, WhatsappSession
from app.repositories.base import BaseRepository


class SessionRepository(BaseRepository[WhatsappSession]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(WhatsappSession, session)

    async def get_by_user(self, user_id: int) -> WhatsappSession | None:
        result = await self._session.execute(
            select(WhatsappSession).where(WhatsappSession.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, user_id: int) -> WhatsappSession:
        session = await self.get_by_user(user_id)
        if not session:
            session = WhatsappSession(user_id=user_id, state=ConversationState.IDLE)
            await self.save(session)
        return session

    async def transition(
        self,
        session: WhatsappSession,
        new_state: ConversationState,
        context: dict | None = None,
    ) -> WhatsappSession:
        session.state = new_state
        # Always stamp _ts so the timeout check in message_processor works
        existing = json.loads(session.context_data) if session.context_data else {}
        if context is not None:
            existing = context
        existing["_ts"] = datetime.now().isoformat()
        session.context_data = json.dumps(existing)
        await self._session.flush()
        return session

    async def get_context(self, session: WhatsappSession) -> dict:
        if not session.context_data:
            return {}
        return json.loads(session.context_data)

    async def reset(self, session: WhatsappSession) -> WhatsappSession:
        session.state = ConversationState.IDLE
        session.context_data = None
        await self._session.flush()
        return session
