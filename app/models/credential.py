"""
app/models/credential.py
Stores per-user NFSe portal credentials. One active credential per user.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class NfseCredential(Base):
    __tablename__ = "nfse_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # Portal identification
    portal_type: Mapped[str] = mapped_column(String(50), default="nacional")
    municipality: Mapped[str] = mapped_column(String(100))
    portal_url: Mapped[str] = mapped_column(Text)

    # Login credentials (password encrypted with Fernet — see app/utils/crypto.py)
    username: Mapped[str] = mapped_column(String(100))
    password: Mapped[str] = mapped_column(Text)

    # Prestador data
    prestador_nome: Mapped[str | None] = mapped_column(String(200))
    prestador_cnpj: Mapped[str | None] = mapped_column(String(20))

    # Tomador defaults (Shopee)
    tomador_cnpj: Mapped[str | None] = mapped_column(String(20))
    tomador_razao_social: Mapped[str | None] = mapped_column(String(200))

    # Service defaults
    service_description: Mapped[str | None] = mapped_column(Text)
    service_aliquota_iss: Mapped[float] = mapped_column(default=2.0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="credentials")

    def __repr__(self) -> str:
        return f"<NfseCredential id={self.id} user={self.user_id} municipality={self.municipality}>"
