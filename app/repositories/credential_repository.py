"""
app/repositories/credential_repository.py
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credential import NfseCredential
from app.repositories.base import BaseRepository


class CredentialRepository(BaseRepository[NfseCredential]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(NfseCredential, session)

    async def get_active_by_user(self, user_id: int) -> NfseCredential | None:
        result = await self._session.execute(
            select(NfseCredential)
            .where(NfseCredential.user_id == user_id, NfseCredential.is_active == True)
            .order_by(NfseCredential.id.desc())
        )
        return result.scalar_one_or_none()

    async def deactivate_all_for_user(self, user_id: int) -> None:
        result = await self._session.execute(
            select(NfseCredential).where(NfseCredential.user_id == user_id)
        )
        for credential in result.scalars().all():
            credential.is_active = False
        await self._session.flush()
