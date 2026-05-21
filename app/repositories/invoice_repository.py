"""
app/repositories/invoice_repository.py
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceStatus
from app.repositories.base import BaseRepository


class InvoiceRepository(BaseRepository[Invoice]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Invoice, session)

    async def list_by_user(self, user_id: int, limit: int = 10) -> list[Invoice]:
        result = await self._session.execute(
            select(Invoice)
            .where(Invoice.user_id == user_id)
            .order_by(Invoice.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_successful_by_user(self, user_id: int) -> list[Invoice]:
        """Retorna todas as notas emitidas com sucesso, sem limite, para cálculo de totais."""
        result = await self._session.execute(
            select(Invoice)
            .where(Invoice.user_id == user_id, Invoice.status == InvoiceStatus.SUCCESS)
            .order_by(Invoice.created_at.asc())
        )
        return list(result.scalars().all())

    async def mark_success(self, invoice: Invoice, number: str, pdf_path: str | None, xml_path: str | None) -> Invoice:
        invoice.status = InvoiceStatus.SUCCESS
        invoice.invoice_number = number
        invoice.pdf_path = pdf_path
        invoice.xml_path = xml_path
        await self._session.flush()
        return invoice

    async def mark_failed(
        self,
        invoice: Invoice,
        error: str,
        *,
        stage: str = "",
        screenshot_path: str | None = None,
    ) -> Invoice:
        invoice.status = InvoiceStatus.FAILED
        invoice.error_message = error
        invoice.failed_at = datetime.now()
        invoice.failed_stage = stage or None
        invoice.screenshot_path = screenshot_path
        await self._session.flush()
        return invoice
