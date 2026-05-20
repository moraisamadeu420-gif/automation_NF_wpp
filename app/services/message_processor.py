"""
app/services/message_processor.py
State-machine that drives the WhatsApp conversation flow.

States:
  IDLE                → check credentials; show menu
  ONBOARDING_*        → multi-step credential collection
  AWAITING_VALUE      → scheduler (or user) triggered; waiting for monetary value
  AWAITING_VALUE_CONFIRM → user confirms the value and period
  PROCESSING          → Playwright emission running (guard against duplicates)
"""
import re

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import CredentialNotFoundError, NfseEmissionError
from app.integrations.evolution.client import evolution_client
from app.integrations.evolution.schemas import WebhookMessage
from app.models.session import ConversationState
from app.repositories.session_repository import SessionRepository
from app.services.nfse_service import NfseService
from app.services.user_service import UserService
from app.utils.period import format_period, previous_week_period

_MENU = (
    "Ola! Escolha uma opcao:\n"
    "1 - Emitir NFS-e da semana\n"
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


def _parse_value(text: str) -> float | None:
    cleaned = re.sub(r"[^\d,.]", "", text)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        v = float(cleaned)
        return v if v > 0 else None
    except ValueError:
        return None


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
        if "@g.us" in number:
            logger.debug("Skipping group message from {}", number)
            return

        sender = number.split("@")[0]
        user, _ = await self._users.get_or_create_user(sender, message.push_name)
        conv = await self._sessions.get_or_create(user.id)

        state = ConversationState(conv.state)
        text = (message.message.text or "").strip()

        logger.info("Message from {} | state: {} | text: '{}'", sender, state, text[:80])

        if state == ConversationState.IDLE:
            await self._handle_idle(sender, user, conv, text)
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
        elif state == ConversationState.ONBOARDING_CITY:
            await self._handle_onboarding_city(sender, user, conv, text)
        elif state == ConversationState.AWAITING_VALUE:
            await self._handle_awaiting_value(sender, user, conv, text)
        elif state == ConversationState.AWAITING_CONFIRMATION:
            await self._handle_value_confirm(sender, user, conv, text)
        elif state == ConversationState.PROCESSING:
            await evolution_client.send_text(sender, "Sua nota esta sendo processada. Aguarde.")

    # ── IDLE ─────────────────────────────────────────────────────────────────

    async def _handle_idle(self, sender, user, conv, text) -> None:
        is_configured = await self._users.user_is_configured(user.id)

        if not is_configured:
            await self._sessions.transition(conv, ConversationState.ONBOARDING_NAME)
            await evolution_client.send_text(sender, _ONBOARDING_WELCOME)
            return

        if text in ("1", "emitir", "nota", "nfse"):
            await self._ask_for_value(sender, conv)
            return

        if text == "2":
            await self._send_history(sender, user)
            return

        if text == "3":
            await self._sessions.transition(conv, ConversationState.ONBOARDING_NAME)
            await evolution_client.send_text(sender, "Vamos reconfigurar. Qual e o seu nome completo?")
            return

        await evolution_client.send_text(sender, _MENU)

    async def _ask_for_value(self, sender: str, conv) -> None:
        start, end = previous_week_period()
        period_str = format_period(start, end)
        ctx = {"periodo": period_str}
        await self._sessions.transition(conv, ConversationState.AWAITING_VALUE, ctx)
        await evolution_client.send_text(
            sender,
            f"Informe o valor dos ganhos da semana de {period_str}:",
        )

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
        await self._sessions.transition(conv, ConversationState.ONBOARDING_CITY, ctx)
        await evolution_client.send_text(
            sender,
            "Informe seu municipio (ex: Campinas/SP, Sao Paulo/SP):",
        )

    async def _handle_onboarding_city(self, sender, user, conv, text) -> None:
        if not text:
            await evolution_client.send_text(sender, "Informe seu municipio:")
            return
        ctx = await self._sessions.get_context(conv)
        ctx["municipality"] = text
        ctx["prestador_nome"] = ctx.get("nome", "")
        ctx["tomador_cnpj"] = "42.446.277/0001-92"
        ctx["tomador_razao_social"] = "SHOPEE COMERCIO DIGITAL DO BRASIL LTDA"
        ctx["service_description"] = "Servicos de entrega e logistica prestados como motorista parceiro SPX Driver"
        ctx["service_aliquota_iss"] = 2.0

        await self._users.save_credential(user.id, {
            "portal_type": ctx.get("portal_type", "nacional"),
            "municipality": ctx["municipality"],
            "portal_url": ctx.get("portal_url", ""),
            "username": ctx["username"],
            "password": ctx["password"],
            "prestador_nome": ctx["prestador_nome"],
            "prestador_cnpj": ctx["prestador_cnpj"],
            "tomador_cnpj": ctx["tomador_cnpj"],
            "tomador_razao_social": ctx["tomador_razao_social"],
            "service_description": ctx["service_description"],
            "service_aliquota_iss": ctx["service_aliquota_iss"],
        })

        await self._sessions.reset(conv)
        await evolution_client.send_text(
            sender,
            f"Configuracao concluida!\n\n"
            f"Portal: {ctx.get('portal_label', ctx.get('portal_type'))}\n"
            f"CNPJ: {ctx['prestador_cnpj']}\n"
            f"Municipio: {text}\n\n"
            "Toda segunda-feira as 9h voce recebera uma mensagem pedindo o valor da semana "
            "para emissao automatica da NFS-e.\n\n"
            "Ou envie '1' a qualquer momento para emitir manualmente."
        )

    # ── AWAITING VALUE ───────────────────────────────────────────────────────

    async def _handle_awaiting_value(self, sender, user, conv, text) -> None:
        if text.upper() in ("CANCELAR", "CANCEL", "0"):
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, "Operacao cancelada. " + _MENU)
            return

        value = _parse_value(text)
        if value is None:
            await evolution_client.send_text(
                sender,
                "Nao consegui identificar o valor. Informe apenas o numero (ex: 697,08).",
            )
            return

        ctx = await self._sessions.get_context(conv)
        period_str = ctx.get("periodo") or format_period(*previous_week_period())
        ctx.update({"valor": value, "periodo": period_str})
        await self._sessions.transition(conv, ConversationState.AWAITING_CONFIRMATION, ctx)

        valor_fmt = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        await evolution_client.send_text(
            sender,
            f"Emitir NFS-e de R$ {valor_fmt} periodo {period_str}? Responda SIM para confirmar.",
        )

    # ── VALUE CONFIRMATION ───────────────────────────────────────────────────

    async def _handle_value_confirm(self, sender, user, conv, text) -> None:
        upper = text.upper()
        if upper not in ("SIM", "S", "NAO", "N", "CANCELAR"):
            await evolution_client.send_text(sender, "Responda SIM para confirmar ou NAO para cancelar.")
            return

        if upper in ("NAO", "N", "CANCELAR"):
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, "Emissao cancelada. " + _MENU)
            return

        ctx = await self._sessions.get_context(conv)
        value: float = ctx.get("valor", 0)
        period: str = ctx.get("periodo", "")

        await self._sessions.transition(conv, ConversationState.PROCESSING)
        await evolution_client.send_text(
            sender,
            f"Iniciando emissao de R$ {value:,.2f}...\nIsso pode levar 1-2 minutos.",
        )

        try:
            invoice = await self._nfse.emit(user.id, value, period)
        except (CredentialNotFoundError, NfseEmissionError) as exc:
            logger.error("Emission error for {}: {}", sender, exc)
            await self._sessions.reset(conv)
            await evolution_client.send_text(
                sender,
                f"Erro ao emitir nota: {exc}\n\nTente novamente ou reconfigure com opcao 3.",
            )
            return

        await self._sessions.reset(conv)

        reply = (
            f"NFS-e emitida com sucesso!\n"
            f"Nota N: {invoice.invoice_number or 'N/A'}\n"
            f"Valor: R$ {value:,.2f}\n"
            f"Periodo: {period}"
        )

        from pathlib import Path
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
            lines.append(f"- Nota {inv.invoice_number or '?'} | R$ {inv.value:,.2f} | {inv.period} | {status_label}")
        await evolution_client.send_text(sender, "\n".join(lines))
