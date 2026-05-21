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
from datetime import datetime

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
    "Olá! O que deseja fazer?\n\n"
    "1 - Emitir NFS-e da semana\n"
    "2 - Ver histórico de notas\n"
    "3 - Atualizar meus dados\n"
    "4 - Ajuda\n"
    "5 - Somar notas fiscais\n\n"
    "💡 Dica: A qualquer momento você pode digitar:\n"
    "• INICIAR — iniciar emissão de NFS-e\n"
    "• TOTAL — somar todas as notas (útil para o IR)\n"
    "• CANCELAR — cancelar a operação atual\n"
    "• VOLTAR — voltar para o passo anterior\n"
    "• ENCERRAR — encerrar o atendimento\n"
    "• MENU — voltar ao menu principal"
)

_HELP_MSG = (
    "📋 Comandos disponíveis:\n\n"
    "1 - Emitir NFS-e da semana\n"
    "2 - Ver histórico de notas\n"
    "3 - Atualizar meus dados\n"
    "5 - Somar notas fiscais\n\n"
    "A qualquer momento:\n"
    "INICIAR — inicia a emissão de NFS-e\n"
    "TOTAL — soma as notas do ano atual\n"
    "TOTAL 2025 — soma as notas de um ano específico\n"
    "CANCELAR — cancela a operação atual\n"
    "VOLTAR — volta ao passo anterior\n"
    "ENCERRAR — encerra o atendimento\n"
    "MENU — volta ao menu principal\n"
    "AJUDA — mostra esta mensagem\n\n"
    "📅 Toda segunda-feira às 9h você recebe automaticamente o pedido de valor para emissão.\n\n"
    "Precisa de suporte? Fale conosco:\nhttps://wa.me/5519971721948"
)

_ONBOARDING_WELCOME = (
    "Bem-vindo ao Bot NFS-e!\n"
    "Vou precisar das suas credenciais do portal nfse.gov.br para emitir notas automaticamente.\n\n"
    "Qual e o seu nome completo?"
)

_BACK_MAP = {
    ConversationState.ONBOARDING_USERNAME: (ConversationState.ONBOARDING_NAME,     "Qual e o seu nome completo?"),
    ConversationState.ONBOARDING_PASSWORD: (ConversationState.ONBOARDING_USERNAME, "Informe seu CNPJ de login (somente numeros ou 00.000.000/0000-00):"),
    ConversationState.ONBOARDING_CITY:     (ConversationState.ONBOARDING_PASSWORD, "Informe sua senha do portal:"),
    ConversationState.ONBOARDING_CONFIRM:  (ConversationState.ONBOARDING_CITY,     "Informe seu municipio (ex: Campinas/SP, Sao Paulo/SP):"),
}


def _format_cnpj(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
    return text


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


def _validate_cnpj(text: str) -> bool:
    return len(re.sub(r"\D", "", text)) == 14


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

        if state == ConversationState.PROCESSING:
            logger.debug("Ignoring message — emission in progress for {}", sender)
            return

        text_upper = text.upper()

        # ── Comandos globais ──────────────────────────────────────────────────
        if text_upper in ("AJUDA", "HELP"):
            await evolution_client.send_text(sender, _HELP_MSG)
            return

        if text_upper.split()[0] in ("TOTAL", "SOMAR", "SOMA", "IR"):
            parts = text_upper.split()
            year = None
            if len(parts) > 1 and parts[1].isdigit():
                year = int(parts[1])
            await self._send_total(sender, user, year=year)
            return

        if text_upper in ("ENCERRAR", "SAIR", "TCHAU"):
            await self._sessions.reset(conv)
            await evolution_client.send_text(
                sender,
                "Até logo! 👋\n\nQuando quiser emitir sua NFS-e é só digitar INICIAR ou MENU.",
            )
            return

        if text_upper in ("INICIAR", "COMECAR", "COMEÇAR", "START"):
            await self._sessions.reset(conv)
            is_configured = await self._users.user_is_configured(user.id)
            if is_configured:
                await self._ask_for_value(sender, conv)
            else:
                await self._sessions.transition(conv, ConversationState.ONBOARDING_NAME)
                await evolution_client.send_text(sender, _ONBOARDING_WELCOME)
            return

        if text_upper in ("CANCELAR", "PARAR") and state != ConversationState.IDLE:
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, f"Ok! Operação cancelada.\n\n{_MENU}")
            return

        if text_upper in ("MENU", "INICIO", "INÍCIO") and state != ConversationState.IDLE:
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, _MENU)
            return

        if text_upper in ("VOLTAR", "CORRIGIR") and state not in (
            ConversationState.IDLE,
            ConversationState.PROCESSING,
        ):
            await self._handle_correct(sender, conv, state)
            return

        if state == ConversationState.IDLE:
            await self._handle_idle(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_NAME:
            await self._handle_onboarding_name(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_USERNAME:
            await self._handle_onboarding_username(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_PASSWORD:
            await self._handle_onboarding_password(sender, user, conv, text)
            await evolution_client.delete_message(number, msg_id)
            await evolution_client.send_text(
                sender,
                "🔒 Senha salva com criptografia.\n"
                "Por segurança, apague a mensagem anterior do chat agora\n"
                "(segure a mensagem → Apagar → Apagar para todos).",
            )
        elif state == ConversationState.ONBOARDING_CITY:
            await self._handle_onboarding_city(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_CONFIRM:
            await self._handle_onboarding_confirm(sender, user, conv, text)
        elif state == ConversationState.AWAITING_VALUE:
            await self._handle_awaiting_value(sender, user, conv, text)
        elif state == ConversationState.AWAITING_CONFIRMATION:
            await self._handle_value_confirm(sender, user, conv, text)

    # ── CORRIGIR / VOLTAR ────────────────────────────────────────────────────

    async def _handle_correct(self, sender, conv, state: ConversationState) -> None:
        prefix = "Ok! Vamos corrigir. "

        if state in _BACK_MAP:
            prev_state, question = _BACK_MAP[state]
            await self._sessions.transition(conv, prev_state)
            await evolution_client.send_text(sender, prefix + question)
            return

        if state == ConversationState.ONBOARDING_NAME:
            await evolution_client.send_text(sender, prefix + "Qual e o seu nome completo?")
            return

        ctx = await self._sessions.get_context(conv)
        period_str = ctx.get("periodo") or format_period(*previous_week_period())
        value_msg = (
            f"Informe o valor exato dos seus ganhos da semana de {period_str} (ex: 697,08).\n\n"
            "⚠️ O valor deve ser identico ao mostrado no app SPX Driver, incluindo centavos. "
            "Valores diferentes podem causar problemas na faturacao."
        )

        if state == ConversationState.AWAITING_CONFIRMATION:
            await self._sessions.transition(conv, ConversationState.AWAITING_VALUE, ctx)

        await evolution_client.send_text(sender, prefix + value_msg)

    # ── IDLE ─────────────────────────────────────────────────────────────────

    async def _handle_idle(self, sender, user, conv, text) -> None:
        is_configured = await self._users.user_is_configured(user.id)

        if not is_configured:
            await self._sessions.transition(conv, ConversationState.ONBOARDING_NAME)
            await evolution_client.send_text(sender, _ONBOARDING_WELCOME)
            return

        text_upper = text.upper()

        if text_upper in ("1", "EMITIR", "NOTA", "NFSE"):
            await self._ask_for_value(sender, conv)
            return

        if text_upper == "2":
            await self._send_history(sender, user)
            return

        if text_upper == "3":
            await self._sessions.transition(conv, ConversationState.ONBOARDING_USERNAME)
            await evolution_client.send_text(
                sender,
                "⚙️ Atualização de dados\n\n"
                "Você irá atualizar os dados que o bot usa para acessar o portal NFS-e em seu nome.\n\n"
                "⚠️ Importante: isso NÃO altera sua senha no portal nfse.gov.br. "
                "Se quiser mudar sua senha no portal, acesse diretamente: https://www.nfse.gov.br\n\n"
                "Vamos começar. Informe seu CNPJ de login:",
            )
            return

        if text_upper == "4":
            await evolution_client.send_text(sender, _HELP_MSG)
            return

        if text_upper == "5":
            await self._send_total(sender, user)
            return

        if text_upper in ("MENU", "INICIO", "INÍCIO", "OI", "OLA", "OLÁ"):
            await evolution_client.send_text(sender, _MENU)
            return

        await evolution_client.send_text(sender, "Não entendi. Digite MENU para ver as opções disponíveis.")

    async def _ask_for_value(self, sender: str, conv) -> None:
        start, end = previous_week_period()
        period_str = format_period(start, end)
        ctx = {"periodo": period_str}
        await self._sessions.transition(conv, ConversationState.AWAITING_VALUE, ctx)
        await evolution_client.send_text(
            sender,
            f"Informe o valor exato dos seus ganhos da semana de {period_str} (ex: 697,08).\n\n"
            "⚠️ O valor deve ser identico ao mostrado no app SPX Driver, incluindo centavos. "
            "Valores diferentes podem causar problemas na faturacao.",
        )

    # ── ONBOARDING ────────────────────────────────────────────────────────────

    async def _handle_onboarding_name(self, sender, user, conv, text) -> None:
        if not text:
            await evolution_client.send_text(sender, "Por favor, informe seu nome.")
            return
        user.name = text
        ctx = {"nome": text}
        await self._sessions.transition(conv, ConversationState.ONBOARDING_USERNAME, ctx)
        await evolution_client.send_text(sender, "Informe seu CNPJ de login no portal nfse.gov.br (somente numeros ou 00.000.000/0000-00):")

    async def _handle_onboarding_username(self, sender, user, conv, text) -> None:
        if not text or not _validate_cnpj(text):
            await evolution_client.send_text(
                sender,
                "CNPJ invalido. Informe no formato 00.000.000/0000-00 (14 digitos):",
            )
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
        ctx["prestador_nome"] = ctx.get("nome") or user.name or ""
        ctx["prestador_cnpj"] = ctx["username"]
        ctx["tomador_cnpj"] = "42.446.277/0001-92"
        ctx["tomador_razao_social"] = "SHOPEE COMERCIO DIGITAL DO BRASIL LTDA"
        ctx["service_description"] = "Servicos de entrega e logistica prestados como motorista parceiro SPX Driver"
        ctx["service_aliquota_iss"] = 2.0

        await self._sessions.transition(conv, ConversationState.ONBOARDING_CONFIRM, ctx)

        cnpj_fmt = _format_cnpj(ctx["username"])
        await evolution_client.send_text(
            sender,
            f"Confirme seus dados:\n\n"
            f"Portal: Emissor Nacional (nfse.gov.br)\n"
            f"Login: {cnpj_fmt}\n"
            f"Senha: ••••••••\n"
            f"CNPJ: {cnpj_fmt}\n"
            f"Municipio: {text}\n\n"
            "Responda SIM para confirmar ou NAO para cancelar.",
        )

    async def _handle_onboarding_confirm(self, sender, user, conv, text) -> None:
        upper = text.upper()
        if upper not in ("SIM", "S", "NAO", "N", "CANCELAR"):
            await evolution_client.send_text(sender, "Responda SIM para confirmar ou NAO para cancelar.")
            return

        if upper in ("NAO", "N", "CANCELAR"):
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, "Configuracao cancelada. " + _MENU)
            return

        ctx = await self._sessions.get_context(conv)
        await self._users.save_credential(user.id, {
            "portal_type": "nacional",
            "municipality": ctx["municipality"],
            "portal_url": "https://www.nfse.gov.br/EmissorNacional/Login",
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
            "Configuracao salva com sucesso!\n\n"
            "Toda segunda-feira as 9h voce recebera uma mensagem pedindo o valor da semana "
            "para emissao automatica da NFS-e.\n\n"
            "Ou envie '1' a qualquer momento para emitir manualmente.",
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
                f"Falha na emissao: {exc}\n\nVerifique suas credenciais com a opcao 3.",
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
        lines = ["Últimas 5 notas emitidas:\n"]
        for inv in invoices:
            status_label = "✅" if inv.status == "success" else "❌"
            lines.append(f"{status_label} Nota {inv.invoice_number or '?'} | R$ {inv.value:,.2f} | {inv.period}")
        await evolution_client.send_text(sender, "\n".join(lines))

    # ── TOTAL / SOMA ─────────────────────────────────────────────────────────

    async def _send_total(self, sender: str, user, year: int | None = None) -> None:
        from app.repositories.invoice_repository import InvoiceRepository
        repo = InvoiceRepository(self._session)
        all_invoices = await repo.list_successful_by_user(user.id)

        if not all_invoices:
            await evolution_client.send_text(sender, "Nenhuma nota emitida ainda.")
            return

        now = datetime.now()
        target_year = year or now.year

        _MESES = {
            1: "jan", 2: "fev", 3: "mar", 4: "abr", 5: "mai", 6: "jun",
            7: "jul", 8: "ago", 9: "set", 10: "out", 11: "nov", 12: "dez",
        }
        _MESES_FULL = {
            1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
            5: "maio", 6: "junho", 7: "julho", 8: "agosto",
            9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
        }

        def fmt(v: float) -> str:
            return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        # Notas do ano alvo
        year_invoices = [inv for inv in all_invoices if inv.created_at.year == target_year]

        # Breakdown mensal do ano alvo
        monthly: dict[int, tuple[float, int]] = {}
        for inv in year_invoices:
            m = inv.created_at.month
            total_m, count_m = monthly.get(m, (0.0, 0))
            monthly[m] = (total_m + float(inv.value), count_m + 1)

        total_year = sum(v for v, _ in monthly.values())
        count_year = sum(c for _, c in monthly.values())

        # Mês atual (só exibe se o ano alvo é o atual)
        month_section = ""
        if target_year == now.year:
            m_val, m_cnt = monthly.get(now.month, (0.0, 0))
            mes_nome = _MESES_FULL[now.month]
            month_section = (
                f"📅 Total do mês — {mes_nome.capitalize()}/{now.year}:\n"
                f"   {fmt(m_val)} — {m_cnt} nota{'s' if m_cnt != 1 else ''}\n\n"
            )

        # Breakdown mensal (só linhas com notas)
        breakdown_lines = []
        for m in sorted(monthly):
            v, c = monthly[m]
            breakdown_lines.append(f"   {_MESES[m]}/{str(target_year)[2:]}: {fmt(v)} ({c})")
        breakdown = "\n".join(breakdown_lines)

        # Total histórico
        total_all = sum(float(inv.value) for inv in all_invoices)
        count_all = len(all_invoices)

        years_range = sorted({inv.created_at.year for inv in all_invoices})
        anos_str = f"{years_range[0]}–{years_range[-1]}" if len(years_range) > 1 else str(years_range[0])

        msg = (
            f"📊 Resumo das suas notas fiscais\n\n"
            f"{month_section}"
            f"📅 Total do ano — {target_year}:\n"
            f"   {fmt(total_year)} — {count_year} nota{'s' if count_year != 1 else ''}\n"
        )
        if breakdown:
            msg += f"\n{breakdown}\n"

        if target_year == now.year and len(years_range) > 1:
            msg += (
                f"\n📊 Total histórico ({anos_str}):\n"
                f"   {fmt(total_all)} — {count_all} notas\n"
            )

        msg += (
            f"\n💡 Para outro ano, envie: TOTAL {target_year - 1}\n"
            "📝 Use o total anual na sua declaração de IR."
        )

        await evolution_client.send_text(sender, msg)
