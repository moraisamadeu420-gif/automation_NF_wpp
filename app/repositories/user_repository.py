"""
app/repositories/user_repository.py
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(User, session)

    async def get_by_whatsapp_number(self, number: str) -> User | None:
        result = await self._session.execute(
            select(User)
            .where(User.whatsapp_number == number)
            .options(selectinload(User.credentials), selectinload(User.session))
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, number: str) -> tuple[User, bool]:
        user = await self.get_by_whatsapp_number(number)
        if user:
            return user, False
        user = User(whatsapp_number=number)
        await self.save(user)
        return user, True
