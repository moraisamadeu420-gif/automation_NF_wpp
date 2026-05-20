"""
app/services/message_processor.py
State-machine that drives the WhatsApp conversation flow.

States:
  IDLE                → user sends anything → check credentials
  ONBOARDING_*        → multi-step credential collection
  AWAITING_IMAGE      → user must send a screenshot
  AWAITING_VALUE_CONFIRM → user confirms the OCR-extracted value
  PROCESSING          → emission is running (guard against duplicate triggers)
"""
import json
from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    CredentialNotFoundError,
    NfseEmissionError,
    OcrExtractionError,
)
from app.integrations.evolution.client import evolution_client
from app.integrations.evolution.schemas import WebhookMessage
from app.models.session import ConversationState
from app.repositories.session_repository import SessionRepository
from app.services.nfse_service import NfseService
from app.services.ocr_service import ocr_service
from app.services.user_service import UserService
from app.utils.file_utils import cleanup_file, save_bytes_to_temp

_MENU = (
    "Ola! Escolha uma opcao:\n"
    "1 - Emitir NFS-e (envie print do SPX Driver)\n"
    "2 - Ver historico de notas\n"
    "3 - Reconfigurar credenciais\n"
    "4 - Ajuda"
)

_ONBOARDING_WELCOME = (
    "Bem-vindo ao Bot NFS-e!\n"
    "Vou precisar das suas credenciais do portal para emitir notas automaticamente.\n\n"
    "Qual e o seu nome completo?"
)

_PORTALS = {
    "1": ("nacional", "Emissor Nacional (nfse.gov.br)", "https://www.nfse.gov.br/EmissorNacional/Login"),
    "2": ("campinas", "Prefeitura de Campinas", "https://nfe.campinas.sp.gov.br"),
}

_PORTAL_MENU = (
    "Qual portal voce usa?\n"
    "1 - Emissor Nacional (nfse.gov.br) — recomendado para MEI\n"
    "2 - Prefeitura de Campinas\n"
    "3 - Outro (informe a URL)"
)


class MessageProcessor:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserService(session)
        self._sessions = SessionRepository(session)
        self._nfse = NfseService(session)

    async def process(self, message: WebhookMessage) -> None:
        number = message.key.remote_jid
        msg_id = message.key.id
        msg_type = message.message_type or "unknown"

        logger.info(
            "Processing message — id: {} | from: {} | fromMe: {} | type: {}",
            msg_id, number, message.key.from_me, msg_type,
        )

        if message.key.from_me:
            logger.debug("Skipping fromMe message — id: {}", msg_id)
            return
        if not message.message:
            logger.debug("Skipping message with empty content — id: {}", msg_id)
            return

        # Ignore group messages
        if "@g.us" in number:
            logger.debug("Skipping group message from {}", number)
            return

        sender = number.split("@")[0]

        user, created = await self._users.get_or_create_user(sender, message.push_name)
        conv = await self._sessions.get_or_create(user.id)

        state = ConversationState(conv.state)
        text = (message.message.text or "").strip()
        has_image = message.message.has_image

        logger.info("Message from {} | state: {} | text: '{}'", sender, state, text[:80])

        if state == ConversationState.IDLE:
            await self._handle_idle(sender, user, conv, text, has_image, message)
        elif state == ConversationState.ONBOARDING_NAME:
            await self._handle_onboarding_name(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_PORTAL:
            await self._handle_onboarding_portal(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_USERNAME:
            await self._handle_onboarding_username(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_PASSWORD:
            await self._handle_onboarding_password(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_MUNICIPALITY:
            await self._handle_onboarding_municipality(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_CNPJ:
            await self._handle_onboarding_cnpj(sender, user, conv, text)
        elif state == ConversationState.AWAITING_IMAGE:
            await self._handle_awaiting_image(sender, user, conv, text, has_image, message)
        elif state == ConversationState.AWAITING_VALUE_CONFIRM:
            await self._handle_value_confirm(sender, user, conv, text)
        elif state == ConversationState.PROCESSING:
            await evolution_client.send_text(sender, "Sua nota esta sendo processada. Aguarde.")

    # ── IDLE ─────────────────────────────────────────────────────────────────

    async def _handle_idle(self, sender, user, conv, text, has_image, message) -> None:
        is_configured = await self._users.user_is_configured(user.id)

        if not is_configured:
            await self._sessions.transition(conv, ConversationState.ONBOARDING_NAME)
            await evolution_client.send_text(sender, _ONBOARDING_WELCOME)
            return

        if has_image or text in ("1", "emitir", "nota", "nfse"):
            await self._sessions.transition(conv, ConversationState.AWAITING_IMAGE)
            if has_image:
                await self._handle_awaiting_image(sender, user, conv, text, has_image, message)
            else:
                await evolution_client.send_text(sender, "Envie o print do seu ganhos no SPX Driver.")
            return

        if text == "2":
            await self._send_history(sender, user)
            return

        if text == "3":
            await self._sessions.transition(conv, ConversationState.ONBOARDING_NAME)
            await evolution_client.send_text(sender, "Vamos reconfigurar. Qual e o seu nome completo?")
            return

        await evolution_client.send_text(sender, _MENU)

    # ── ONBOARDING ────────────────────────────────────────────────────────────

    async def _handle_onboarding_name(self, sender, user, conv, text) -> None:
        if not text:
            await evolution_client.send_text(sender, "Por favor, informe seu nome.")
            return
        user.name = text
        ctx = {"nome": text}
        await self._sessions.transition(conv, ConversationState.ONBOARDING_PORTAL, ctx)
        await evolution_client.send_text(sender, _PORTAL_MENU)

    async def _handle_onboarding_portal(self, sender, user, conv, text) -> None:
        ctx = await self._sessions.get_context(conv)
        if text in _PORTALS:
            key, label, url = _PORTALS[text]
            ctx.update({"portal_type": key, "portal_url": url, "portal_label": label})
            await self._sessions.transition(conv, ConversationState.ONBOARDING_USERNAME, ctx)
            await evolution_client.send_text(sender, f"Portal: {label}\n\nInforme seu CPF/CNPJ de login:")
        elif text == "3":
            ctx.update({"portal_type": "outro"})
            await self._sessions.transition(conv, ConversationState.ONBOARDING_MUNICIPALITY, ctx)
            await evolution_client.send_text(sender, "Informe a URL completa do portal NFSe da sua prefeitura:")
        else:
            await evolution_client.send_text(sender, "Opcao invalida. " + _PORTAL_MENU)

    async def _handle_onboarding_municipality(self, sender, user, conv, text) -> None:
        ctx = await self._sessions.get_context(conv)
        if text.startswith("http"):
            ctx.update({"portal_url": text, "municipality": "outro"})
            await self._sessions.transition(conv, ConversationState.ONBOARDING_USERNAME, ctx)
            await evolution_client.send_text(sender, "URL registrada. Informe seu CPF/CNPJ de login:")
        else:
            ctx.update({"municipality": text})
            await self._sessions.transition(conv, ConversationState.ONBOARDING_USERNAME, ctx)
            await evolution_client.send_text(sender, "Informe seu CPF/CNPJ de login no portal:")

    async def _handle_onboarding_username(self, sender, user, conv, text) -> None:
        if not text:
            await evolution_client.send_text(sender, "Informe seu CPF/CNPJ:")
            return
        ctx = await self._sessions.get_context(conv)
        ctx["username"] = text
        await self._sessions.transition(conv, ConversationState.ONBOARDING_PASSWORD, ctx)
        await evolution_client.send_text(sender, "Informe sua senha do portal:")

    async def _handle_onboarding_password(self, sender, user, conv, text) -> None:
        if not text:
            await evolution_client.send_text(sender, "Informe sua senha:")
            return
        ctx = await self._sessions.get_context(conv)
        ctx["password"] = text
        await self._sessions.transition(conv, ConversationState.ONBOARDING_CNPJ, ctx)
        await evolution_client.send_text(sender, "Informe seu CNPJ (formato: 00.000.000/0000-00):")

    async def _handle_onboarding_cnpj(self, sender, user, conv, text) -> None:
        if not text:
            await evolution_client.send_text(sender, "Informe seu CNPJ:")
            return
        ctx = await self._sessions.get_context(conv)
        ctx["prestador_cnpj"] = text
        ctx["prestador_nome"] = ctx.get("nome", "")
        ctx["municipality"] = ctx.get("portal_type", "nacional")
        ctx["tomador_cnpj"] = "42.446.277/0001-92"
        ctx["tomador_razao_social"] = "SHOPEE COMERCIO DIGITAL DO BRASIL LTDA"
        ctx["service_description"] = "Servicos de entrega e logistica prestados como motorista parceiro SPX Driver"
        ctx["service_favorite_name"] = ctx.get("nome", "").split()[0] if ctx.get("nome") else ""
        ctx["service_aliquota_iss"] = 2.0

        await self._users.save_credential(user.id, {
            "portal_type": ctx.get("portal_type", "nacional"),
            "municipality": ctx.get("municipality", "nacional"),
            "portal_url": ctx.get("portal_url", ""),
            "username": ctx["username"],
            "password": ctx["password"],
            "prestador_nome": ctx["prestador_nome"],
            "prestador_cnpj": ctx["prestador_cnpj"],
            "tomador_cnpj": ctx["tomador_cnpj"],
            "tomador_razao_social": ctx["tomador_razao_social"],
            "service_description": ctx["service_description"],
            "service_favorite_name": ctx["service_favorite_name"],
            "service_aliquota_iss": ctx["service_aliquota_iss"],
        })

        await self._sessions.reset(conv)
        await evolution_client.send_text(
            sender,
            f"Configuracao concluida!\n\n"
            f"Portal: {ctx.get('portal_label', ctx.get('portal_type'))}\n"
            f"CNPJ: {text}\n\n"
            "Agora envie o print dos seus ganhos no SPX Driver para emitir sua NFS-e."
        )

    # ── AWAITING IMAGE ───────────────────────────────────────────────────────

    async def _handle_awaiting_image(self, sender, user, conv, text, has_image, message) -> None:
        if not has_image:
            if text.lower() in ("cancelar", "cancel", "0"):
                await self._sessions.reset(conv)
                await evolution_client.send_text(sender, "Operacao cancelada. " + _MENU)
                return
            await evolution_client.send_text(sender, "Envie uma imagem do print do SPX Driver.")
            return

        await evolution_client.send_text(sender, "Imagem recebida. Lendo ganhos...")

        image_path: Path | None = None
        try:
            img_bytes = await evolution_client.download_media(message.key.id)
            image_path = save_bytes_to_temp(img_bytes, suffix=".jpg")
            data = await ocr_service.extract_earnings_from_image(image_path)
        except OcrExtractionError as exc:
            logger.warning("OCR failed for {}: {}", sender, exc)
            await evolution_client.send_text(
                sender,
                "Nao consegui ler o valor da imagem. Tente novamente com uma imagem mais clara."
            )
            return
        finally:
            cleanup_file(image_path)

        ctx = {"valor": data["valor"], "periodo": data["periodo"]}
        await self._sessions.transition(conv, ConversationState.AWAITING_VALUE_CONFIRM, ctx)
        await evolution_client.send_text(
            sender,
            f"Encontrei os seguintes dados:\n"
            f"Valor: R$ {data['valor']:.2f}\n"
            f"Periodo: {data['periodo']}\n\n"
            "Confirma a emissao da NFS-e?\n"
            "Responda: SIM ou NAO"
        )

    # ── VALUE CONFIRMATION ───────────────────────────────────────────────────

    async def _handle_value_confirm(self, sender, user, conv, text) -> None:
        if text.upper() not in ("SIM", "S", "NAO", "N", "NAO", "CANCELAR"):
            await evolution_client.send_text(sender, "Responda SIM para confirmar ou NAO para cancelar.")
            return

        if text.upper() in ("NAO", "N", "CANCELAR"):
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, "Emissao cancelada.")
            return

        ctx = await self._sessions.get_context(conv)
        value = ctx.get("valor", 0)
        period = ctx.get("periodo", "")

        await self._sessions.transition(conv, ConversationState.PROCESSING)
        await evolution_client.send_text(
            sender,
            f"Iniciando emissao de R$ {value:.2f}...\nIsso pode levar 1-2 minutos."
        )

        try:
            invoice = await self._nfse.emit(user.id, value, period)
        except (CredentialNotFoundError, NfseEmissionError) as exc:
            logger.error("Emission error for {}: {}", sender, exc)
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, f"Erro ao emitir nota: {exc}\n\nTente novamente ou reconfigure com opcao 3.")
            return

        await self._sessions.reset(conv)

        reply = (
            f"NFS-e emitida com sucesso!\n"
            f"Nota N: {invoice.invoice_number or 'N/A'}\n"
            f"Valor: R$ {value:.2f}\n"
            f"Periodo: {period}"
        )

        if invoice.pdf_path and Path(invoice.pdf_path).exists():
            await evolution_client.send_text(sender, reply)
            await evolution_client.send_pdf(
                sender,
                Path(invoice.pdf_path),
                caption=f"NFS-e {invoice.invoice_number or ''}",
            )
        else:
            await evolution_client.send_text(sender, reply + "\n\n(PDF nao disponivel para download)")

    # ── HISTORY ──────────────────────────────────────────────────────────────

    async def _send_history(self, sender: str, user) -> None:
        from app.repositories.invoice_repository import InvoiceRepository
        repo = InvoiceRepository(self._session)
        invoices = await repo.list_by_user(user.id, limit=5)
        if not invoices:
            await evolution_client.send_text(sender, "Nenhuma nota emitida ainda.")
            return
        lines = ["Ultimas 5 notas emitidas:\n"]
        for inv in invoices:
            status_label = "OK" if inv.status == "success" else "FALHOU"
            lines.append(f"- Nota {inv.invoice_number or '?'} | R$ {inv.value:.2f} | {inv.period} | {status_label}")
        await evolution_client.send_text(sender, "\n".join(lines))
