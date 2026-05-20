"""
app/services/user_service.py
User lifecycle and credential management.
"""
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credential import NfseCredential
from app.models.user import User
from app.repositories.credential_repository import CredentialRepository
from app.repositories.user_repository import UserRepository


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)
        self._credentials = CredentialRepository(session)

    async def get_or_create_user(self, whatsapp_number: str, push_name: str | None = None) -> tuple[User, bool]:
        user, created = await self._users.get_or_create(whatsapp_number)
        if created and push_name:
            user.name = push_name
            await self._users.save(user)
            logger.info("New user registered: {} ({})", whatsapp_number, push_name)
        return user, created

    async def get_active_credential(self, user_id: int) -> NfseCredential | None:
        return await self._credentials.get_active_by_user(user_id)

    async def save_credential(self, user_id: int, credential_data: dict) -> NfseCredential:
        await self._credentials.deactivate_all_for_user(user_id)
        credential = NfseCredential(user_id=user_id, **credential_data)
        await self._credentials.save(credential)
        logger.info("Credential saved for user {}", user_id)
        return credential

    async def user_is_configured(self, user_id: int) -> bool:
        credential = await self._credentials.get_active_by_user(user_id)
        return credential is not None
