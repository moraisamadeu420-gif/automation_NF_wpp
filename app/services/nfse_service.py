"""
app/services/nfse_service.py
Orchestrates NFSe emission: selects adapter, runs Playwright in thread
executor, persists invoice, and returns result.
"""
import asyncio
from datetime import datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import get_adapter
from app.adapters.base_adapter import EmissionRequest
from app.core.config import settings
from app.core.exceptions import CredentialNotFoundError, NfseEmissionError
from app.models.invoice import Invoice, InvoiceStatus
from app.repositories.credential_repository import CredentialRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.utils.crypto import decrypt


class NfseService:
    def __init__(self, session: AsyncSession) -> None:
        self._invoices = InvoiceRepository(session)
        self._credentials = CredentialRepository(session)

    async def emit(self, user_id: int, value: float, period: str) -> Invoice:
        credential = await self._credentials.get_active_by_user(user_id)
        if not credential:
            raise CredentialNotFoundError(user_id)

        invoice = Invoice(
            user_id=user_id,
            value=value,
            period=period,
            municipality=credential.municipality,
            status=InvoiceStatus.PROCESSING,
        )
        await self._invoices.save(invoice)

        request = EmissionRequest(
            value=value,
            period=period,
            username=credential.username,
            password=decrypt(credential.password),
            portal_url=credential.portal_url,
            prestador_nome=credential.prestador_nome or "",
            prestador_cnpj=credential.prestador_cnpj or "",
            tomador_cnpj=credential.tomador_cnpj or "",
            tomador_razao_social=credential.tomador_razao_social or "",
            service_description=self._build_description(credential.service_description, period),
            municipio=credential.municipality or "Campinas",
            aliquota_iss=credential.service_aliquota_iss,
            headless=settings.browser_headless,
            slow_mo=settings.browser_slow_mo,
            user_id=user_id,
        )

        adapter = get_adapter(credential.portal_type)

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                adapter.emit,
                request,
            )
        except NfseEmissionError as exc:
            logger.error("Emission failed for user {} at stage '{}': {}", user_id, exc.stage, exc)
            await self._invoices.mark_failed(
                invoice,
                str(exc),
                stage=exc.stage,
                screenshot_path=exc.screenshot_path,
            )
            raise

        await self._invoices.mark_success(
            invoice,
            number=result.invoice_number or "",
            pdf_path=str(result.pdf_path) if result.pdf_path else None,
            xml_path=str(result.xml_path) if result.xml_path else None,
        )

        logger.info(
            "Invoice emitted — user: {} | number: {} | value: R$ {:.2f}",
            user_id, result.invoice_number, value,
        )
        return invoice

    @staticmethod
    def _build_description(template: str | None, period: str) -> str:
        base = template or "Serviços de entrega e logística prestados como motorista parceiro SPX Driver"
        return f"{base} - Período: {period}"
