"""
app/models/session.py
Per-user WhatsApp conversation state machine.
"""
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class ConversationState(str, Enum):
    IDLE = "idle"
    ONBOARDING_NAME = "onboarding_name"
    ONBOARDING_USERNAME = "onboarding_username"
    ONBOARDING_PASSWORD = "onboarding_password"
    ONBOARDING_CITY = "onboarding_city"
    ONBOARDING_CONFIRM = "onboarding_confirm"
    AWAITING_VALUE = "awaiting_value"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    PROCESSING = "processing"
    CANCELLING_SUBSCRIPTION = "cancelling_subscription"
    SUBSCRIPTION_MENU = "subscription_menu"


class WhatsappSession(Base):
    __tablename__ = "whatsapp_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)

    state: Mapped[str] = mapped_column(String(50), default=ConversationState.IDLE)

    # Temporary context stored as JSON string (value, period, etc.)
    context_data: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="session")

    def __repr__(self) -> str:
        return f"<WhatsappSession user={self.user_id} state={self.state}>"
