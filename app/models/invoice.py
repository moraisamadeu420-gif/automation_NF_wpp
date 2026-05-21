"""
app/models/invoice.py
Invoice emission history and status tracking.
"""
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    invoice_number: Mapped[str | None] = mapped_column(String(50))
    value: Mapped[float] = mapped_column(Numeric(10, 2))
    period: Mapped[str | None] = mapped_column(String(100))
    municipality: Mapped[str | None] = mapped_column(String(100))

    status: Mapped[str] = mapped_column(String(20), default=InvoiceStatus.PENDING)
    error_message: Mapped[str | None] = mapped_column(Text)

    pdf_path: Mapped[str | None] = mapped_column(Text)
    xml_path: Mapped[str | None] = mapped_column(Text)

    # Failure diagnostics
    failed_at: Mapped[datetime | None] = mapped_column(DateTime)
    failed_stage: Mapped[str | None] = mapped_column(String(50))
    screenshot_path: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="invoices")

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} number={self.invoice_number} status={self.status}>"
