"""
app/services/user_service.py
User lifecycle, credential management, and subscription management.
"""
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.credential import NfseCredential
from app.models.user import User
from app.repositories.credential_repository import CredentialRepository
from app.repositories.user_repository import UserRepository
from app.utils.crypto import encrypt


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

    async def get_by_number(self, whatsapp_number: str) -> User | None:
        return await self._users.get_by_whatsapp_number(whatsapp_number)

    async def list_all(self) -> list[User]:
        return await self._users.list_all()

    async def get_active_credential(self, user_id: int) -> NfseCredential | None:
        return await self._credentials.get_active_by_user(user_id)

    async def save_credential(self, user_id: int, credential_data: dict) -> NfseCredential:
        data = dict(credential_data)
        if "password" in data:
            data["password"] = encrypt(data["password"])

        existing = await self._credentials.get_active_by_user(user_id)
        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
            await self._credentials.save(existing)
            logger.info("Credential updated for user {}", user_id)
            return existing
        credential = NfseCredential(user_id=user_id, **data)
        await self._credentials.save(credential)
        logger.info("Credential created for user {}", user_id)
        return credential

    async def user_is_configured(self, user_id: int) -> bool:
        credential = await self._credentials.get_active_by_user(user_id)
        return credential is not None

    # ── Subscription management ───────────────────────────────────────────────

    async def activate_trial(self, user: User) -> User:
        user.subscription_expires_at = datetime.now() + timedelta(days=settings.trial_days)
        await self._users.save(user)
        logger.info("Trial activated for user {} — expires {}", user.id, user.subscription_expires_at.date())
        return user

    async def activate_subscription(self, user: User, days: int) -> User:
        now = datetime.now()
        base = user.subscription_expires_at if (user.subscription_expires_at and user.subscription_expires_at > now) else now
        user.subscription_expires_at = base + timedelta(days=days)
        await self._users.save(user)
        logger.info("Subscription activated for user {} — expires {}", user.id, user.subscription_expires_at.date())
        return user

    async def cancel_subscription(self, user: User) -> User:
        user.subscription_cancelled_at = datetime.now()
        await self._users.save(user)
        logger.info(
            "Subscription cancelled for user {} — access until {}",
            user.id,
            user.subscription_expires_at,
        )
        return user

    async def block_user(self, user: User) -> User:
        user.is_blocked = True
        await self._users.save(user)
        logger.info("User {} blocked", user.id)
        return user

    async def unblock_user(self, user: User) -> User:
        user.is_blocked = False
        await self._users.save(user)
        logger.info("User {} unblocked", user.id)
        return user

    @staticmethod
    def subscription_active(user: User) -> bool:
        """None = legacy/unlimited user; date in future = active; past = expired."""
        if user.subscription_expires_at is None:
            return True  # legacy users are not blocked
        return user.subscription_expires_at > datetime.now()

    @staticmethod
    def days_remaining(user: User) -> int:
        if user.subscription_expires_at is None:
            return 9999
        delta = user.subscription_expires_at - datetime.now()
        return max(0, delta.days)

    @staticmethod
    def is_cancelled(user: User) -> bool:
        return user.subscription_cancelled_at is not None

    @staticmethod
    def is_trial(user: User) -> bool:
        """True if user was created recently enough to still be in trial window."""
        if user.subscription_expires_at is None:
            return False
        # If the subscription was set within trial_days of creation, it's a trial
        trial_cutoff = user.created_at + timedelta(days=settings.trial_days + 1)
        return user.subscription_expires_at <= trial_cutoff
