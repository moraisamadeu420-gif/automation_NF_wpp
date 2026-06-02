"""
app/services/message_processor.py
State-machine that drives the WhatsApp conversation flow.

States:
  IDLE                → check credentials; show menu
  ONBOARDING_*        → multi-step credential collection
  AWAITING_VALUE      → scheduler (or user) triggered; waiting for monetary value
  AWAITING_CONFIRMATION → user confirms the value and period
  PROCESSING          → Playwright emission running (guard against duplicates)
"""
import asyncio
import re
import unicodedata
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import CredentialNotFoundError, NfseEmissionError
from app.integrations.anthropic.client import anthropic_client
from app.integrations.evolution.client import evolution_client
from app.integrations.evolution.schemas import WebhookMessage
from app.models.session import ConversationState
from app.repositories.session_repository import SessionRepository
from app.services.nfse_service import NfseService
from app.services.user_service import UserService
from app.utils.period import format_period, previous_week_period


def _normalize(text: str) -> str:
    """Upper-case and strip accents so 'horário' == 'HORARIO'."""
    nfkd = unicodedata.normalize("NFD", text)
    return nfkd.encode("ascii", "ignore").decode("ascii").upper()


_QUESTION_STARTERS = (
    "OQUE ", "O QUE ", "PRA QUE", "PARA QUE", "COMO ", "QUANDO ",
    "ONDE ", "POR QUE", "QUAL ", "QUAIS ", "TEM COMO", "SERVE ",
    "O BOT", "ISSO E", "ISSO É",
)


def _is_likely_question(text: str) -> bool:
    if "?" in text:
        return True
    upper = _normalize(text)
    return any(upper.startswith(s) for s in _QUESTION_STARTERS)


_MENU = (
    "O que deseja fazer?\n\n"
    "1 - Emitir NFS-e\n"
    "2 - Histórico de notas\n"
    "3 - Atualizar dados\n"
    "4 - Suporte\n"
    "5 - Assinaturas\n"
    "6 - Somar notas\n"
    "7 - Configurar horário do lembrete semanal"
)

_SUBSCRIPTION_SUBMENU = (
    "Assinaturas:\n\n"
    "1 - Assinar / Renovar\n"
    "2 - Cancelar assinatura"
)

_HELP_MSG = (
    "Comandos:\n\n"
    "INICIAR — emitir NFS-e\n"
    "TOTAL — soma do mês\n"
    "ASSINAR — renovar assinatura\n"
    "REEMBOLSO — solicitar reembolso\n"
    "CANCELAR — cancelar operação\n"
    "VOLTAR — passo anterior\n"
    "MENU — menu principal\n"
    "HORARIO HH:MM — alterar horário do lembrete semanal"
)

_ONBOARDING_WELCOME = "Seu nome completo:"

_BACK_MAP = {
    ConversationState.ONBOARDING_USERNAME: (ConversationState.ONBOARDING_NAME,     "Qual e o seu nome completo?"),
    ConversationState.ONBOARDING_PASSWORD: (ConversationState.ONBOARDING_USERNAME, "Informe seu CNPJ de login (somente numeros ou 00.000.000/0000-00):"),
    ConversationState.ONBOARDING_CITY:     (ConversationState.ONBOARDING_PASSWORD, "Informe sua senha do portal:"),
    ConversationState.ONBOARDING_CONFIRM:  (ConversationState.ONBOARDING_CITY,     "Informe seu municipio (ex: Campinas/SP, Sao Paulo/SP):"),
}

_ONBOARDING_STATES = {
    ConversationState.ONBOARDING_WELCOME,
    ConversationState.ONBOARDING_NAME,
    ConversationState.ONBOARDING_USERNAME,
    ConversationState.ONBOARDING_PASSWORD,
    ConversationState.ONBOARDING_CITY,
    ConversationState.ONBOARDING_CONFIRM,
}

# States where the subscription check is skipped (allow even after expiry)
_SUBSCRIPTION_EXEMPT_STATES = _ONBOARDING_STATES | {
    ConversationState.CANCELLING_SUBSCRIPTION,
    ConversationState.SUBSCRIPTION_MENU,
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


def _with_back(text: str) -> str:
    return f"{text}\n\n↩ VOLTAR"


# Sessão ativa sem resposta do usuário → reset automático
_SESSION_TIMEOUT = timedelta(minutes=60)
_PROCESSING_TIMEOUT = timedelta(minutes=10)


def _is_admin(sender: str) -> bool:
    admin = re.sub(r"\D", "", settings.admin_number)
    return bool(admin) and sender == admin


class MessageProcessor:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserService(session)
        self._sessions = SessionRepository(session)
        self._nfse = NfseService(session)

    async def _faq_intercept(self, sender: str, text: str, reprompt: str) -> bool:
        """
        If text looks like a question, answer with Claude and re-prompt the current step.
        Returns True if handled as FAQ (caller should return early).
        """
        if not _is_likely_question(text):
            return False
        result = await anthropic_client.classify_or_answer(text)
        if result.get("action") == "faq":
            await evolution_client.send_text(sender, result["answer"])
            await evolution_client.send_text(sender, reprompt)
            return True
        return False

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

        # ── Tipo de mensagem não suportado ────────────────────────────────────
        _TEXT_TYPES = {"conversation", "extendedTextMessage", None}
        if msg_type not in _TEXT_TYPES:
            logger.debug("Unsupported message type '{}' from {}", msg_type, sender)
            await evolution_client.send_text(
                sender,
                "Só consigo ler mensagens de texto. Envie sua resposta por escrito.",
            )
            return

        user, is_new_user = await self._users.get_or_create_user(sender, message.push_name)
        conv = await self._sessions.get_or_create(user.id)

        state = ConversationState(conv.state)
        text = (message.message.text or "").strip()

        # Salva o código do afiliado se a mensagem inicial vier com REF:codigo
        if _normalize(text).startswith("REF:") and not user.affiliate_code:
            code = text[4:].strip().lower()
            if code:
                user.affiliate_code = code
                await self._session.commit()
                logger.info("Affiliate code '{}' saved for user {}", code, sender)
            text = ""

        logger.info("Message from {} | state: {} | text: '{}'", sender, state, text[:80])

        # ── Timeout: reseta sessão inativa ────────────────────────────────────
        if state != ConversationState.IDLE:
            ctx_raw = await self._sessions.get_context(conv)
            ts_str = ctx_raw.get("_ts")
            if ts_str:
                elapsed = datetime.now() - datetime.fromisoformat(ts_str)
                timeout = _PROCESSING_TIMEOUT if state == ConversationState.PROCESSING else _SESSION_TIMEOUT
                if elapsed > timeout:
                    logger.info("Session timeout ({}) for {} — resetting", elapsed, sender)
                    await self._sessions.reset(conv)
                    state = ConversationState.IDLE
                    await evolution_client.send_text(
                        sender,
                        "Sessão encerrada por inatividade.\n\nEnvie 1 para emitir ou MENU para ver as opções.",
                    )
                    return
            elif state == ConversationState.PROCESSING:
                # PROCESSING sem timestamp = estado travado, reseta
                await self._sessions.reset(conv)
                state = ConversationState.IDLE

        # ── Admin commands (always allowed) ──────────────────────────────────
        if _is_admin(sender):
            handled = await self._handle_admin(sender, text)
            if handled:
                return

        # ── Access control ───────────────────────────────────────────────────
        if user.is_blocked:
            await evolution_client.send_text(
                sender,
                "⛔ Sua conta está bloqueada. Entre em contato com o suporte:\nhttps://wa.me/5519971721948",
            )
            return

        text_upper = _normalize(text)

        # ── Comprovante MP: número puro de 8-13 dígitos ─────────────────────
        # Só verifica fora do onboarding/emissão para não confundir CNPJ ou valor
        _RECEIPT_EXEMPT = _ONBOARDING_STATES | {
            ConversationState.AWAITING_VALUE,
            ConversationState.AWAITING_CONFIRMATION,
            ConversationState.PROCESSING,
        }
        _stripped = text.strip().replace(" ", "")
        if (state not in _RECEIPT_EXEMPT
                and _stripped.isdigit()
                and 8 <= len(_stripped) <= 13):
            await self._handle_payment_receipt(sender, user, _stripped)
            return

        if text_upper in ("REEMBOLSO", "REEMBOLSAR", "REEMBOLSO PAGAMENTO"):
            await self._handle_refund_request(sender, user)
            return

        if text_upper == "HORARIO" or text_upper.startswith("HORARIO "):
            await self._handle_set_reminder(sender, user, conv, text)
            return

        if state not in _SUBSCRIPTION_EXEMPT_STATES and not UserService.subscription_active(user):
            await self._send_subscription_expired(sender, user)
            return

        # ── Comandos globais ──────────────────────────────────────────────────
        if text_upper in ("AJUDA", "HELP"):
            await evolution_client.send_text(sender, _HELP_MSG)
            return

        if text_upper in ("CANCELAR ASSINATURA", "CANCELAR SUBSCRICAO", "CANCELAR PLANO"):
            await self._start_cancellation(sender, user, conv)
            return

        if text_upper in ("ASSINAR", "REATIVAR", "RENOVAR", "RENOVAR ASSINATURA"):
            await self._send_subscription_link(sender, user)
            return

        _parts = text_upper.split()
        if _parts and _parts[0] in ("TOTAL", "SOMAR", "SOMA", "IR"):
            parts = _parts
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

        if text_upper in ("CANCELAR", "PARAR") and state not in (ConversationState.IDLE, ConversationState.PROCESSING):
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
            await self._handle_idle(sender, user, conv, text, is_new_user)
        elif state == ConversationState.ONBOARDING_WELCOME:
            await self._handle_onboarding_welcome(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_NAME:
            await self._handle_onboarding_name(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_USERNAME:
            await self._handle_onboarding_username(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_PASSWORD:
            await self._handle_onboarding_password(sender, user, conv, text, number, msg_id)
        elif state == ConversationState.ONBOARDING_CITY:
            await self._handle_onboarding_city(sender, user, conv, text)
        elif state == ConversationState.ONBOARDING_CONFIRM:
            await self._handle_onboarding_confirm(sender, user, conv, text)
        elif state == ConversationState.AWAITING_VALUE:
            await self._handle_awaiting_value(sender, user, conv, text)
        elif state == ConversationState.AWAITING_CONFIRMATION:
            await self._handle_value_confirm(sender, user, conv, text)
        elif state == ConversationState.CANCELLING_SUBSCRIPTION:
            await self._handle_cancellation_confirm(sender, user, conv, text)
        elif state == ConversationState.SUBSCRIPTION_MENU:
            await self._handle_subscription_menu(sender, user, conv, text)
        elif state == ConversationState.AWAITING_REMINDER:
            await self._handle_awaiting_reminder(sender, user, conv, text)

    # ── ADMIN ────────────────────────────────────────────────────────────────

    _ADMIN_HELP = (
        "Comandos admin:\n\n"
        "ATIVAR <número> — ativa por {days} dias (padrão)\n"
        "ATIVAR <número> <dias> — ativa por N dias\n"
        "CANCELAR <número> — cancela assinatura do usuário\n"
        "STATUS <número> — situação da assinatura\n"
        "LISTAR — todos os usuários com status\n"
        "BLOQUEAR <número> — bloqueia acesso\n"
        "DESBLOQUEAR <número> — remove bloqueio\n"
        "ADMIN — exibe este menu"
    )

    async def _handle_admin(self, sender: str, text: str) -> bool:
        """Returns True if an admin command was handled."""
        parts = text.split()
        if not parts:
            return False
        cmd = parts[0].upper()

        if cmd == "ADMIN":
            msg = self._ADMIN_HELP.format(days=settings.subscription_days)
            await evolution_client.send_text(sender, msg)
            return True

        if cmd == "ATIVAR" and len(parts) >= 2:
            target_number = re.sub(r"\D", "", parts[1])
            # dias opcional — padrão = subscription_days do plano
            if len(parts) >= 3 and parts[2].isdigit():
                days = int(parts[2])
            else:
                days = settings.subscription_days
            target = await self._users.get_by_number(target_number)
            if not target:
                await evolution_client.send_text(sender, f"Usuário {target_number} não encontrado.")
                return True
            await self._users.activate_subscription(target, days)
            expires = target.subscription_expires_at
            await evolution_client.send_text(
                sender,
                f"✅ Assinatura ativada para {target_number} por {days} dias.\nExpira em: {expires.strftime('%d/%m/%Y') if expires else '?'}",
            )
            await evolution_client.send_text(
                target_number,
                f"✅ Sua assinatura foi ativada!\nAcesso garantido até {expires.strftime('%d/%m/%Y') if expires else '?'}.",
            )
            return True

        if cmd == "LISTAR":
            await self._handle_admin_listar(sender)
            return True

        if cmd == "CANCELAR" and len(parts) >= 2:
            target_number = re.sub(r"\D", "", parts[1])
            target = await self._users.get_by_number(target_number)
            if not target:
                await evolution_client.send_text(sender, f"Usuário {target_number} não encontrado.")
                return True
            if not UserService.subscription_active(target):
                await evolution_client.send_text(sender, f"Usuário {target_number} não tem assinatura ativa.")
                return True
            await self._users.cancel_subscription(target)
            expires = target.subscription_expires_at
            expires_str = expires.strftime("%d/%m/%Y") if expires else "?"
            await evolution_client.send_text(
                sender,
                f"✅ Assinatura de {target_number} cancelada. Acesso até {expires_str}.",
            )
            await evolution_client.send_text(
                target_number,
                f"ℹ️ Sua assinatura foi cancelada pelo administrador.\nVocê tem acesso até {expires_str}.\n\nDigite ASSINAR para reativar.",
            )
            return True

        if cmd == "BLOQUEAR" and len(parts) >= 2:
            target_number = re.sub(r"\D", "", parts[1])
            target = await self._users.get_by_number(target_number)
            if not target:
                await evolution_client.send_text(sender, f"Usuário {target_number} não encontrado.")
                return True
            await self._users.block_user(target)
            await evolution_client.send_text(sender, f"⛔ Usuário {target_number} bloqueado.")
            return True

        if cmd == "DESBLOQUEAR" and len(parts) >= 2:
            target_number = re.sub(r"\D", "", parts[1])
            target = await self._users.get_by_number(target_number)
            if not target:
                await evolution_client.send_text(sender, f"Usuário {target_number} não encontrado.")
                return True
            await self._users.unblock_user(target)
            await evolution_client.send_text(sender, f"✅ Usuário {target_number} desbloqueado.")
            return True

        if cmd == "STATUS" and len(parts) >= 2:
            target_number = re.sub(r"\D", "", parts[1])
            target = await self._users.get_by_number(target_number)
            if not target:
                await evolution_client.send_text(sender, f"Usuário {target_number} não encontrado.")
                return True
            days = UserService.days_remaining(target)
            expires = target.subscription_expires_at
            status = "ATIVO" if UserService.subscription_active(target) else "EXPIRADO"
            blocked = " | BLOQUEADO" if target.is_blocked else ""
            expires_str = expires.strftime("%d/%m/%Y") if expires else "ilimitado (legado)"
            await evolution_client.send_text(
                sender,
                f"👤 {target.name or target_number}\n"
                f"Status: {status}{blocked}\n"
                f"Expira: {expires_str}\n"
                f"Dias restantes: {days}",
            )
            return True

        return False

    async def _handle_admin_listar(self, sender: str) -> None:
        users = await self._users.list_all()
        if not users:
            await evolution_client.send_text(sender, "Nenhum usuário cadastrado.")
            return

        now = datetime.now()
        lines: list[str] = []
        totals = {"ativos": 0, "expirando": 0, "expirados": 0, "bloqueados": 0, "trial": 0}

        for u in users:
            number = u.whatsapp_number
            name = u.name or "—"
            blocked = u.is_blocked
            active = UserService.subscription_active(u)
            days = UserService.days_remaining(u)
            trial = UserService.is_trial(u)
            cancelled = UserService.is_cancelled(u)
            expires = u.subscription_expires_at
            expires_str = expires.strftime("%d/%m") if expires else "ilimitado"

            if blocked:
                icon = "⛔"
                status = "bloqueado"
                totals["bloqueados"] += 1
            elif not active:
                icon = "❌"
                status = f"expirado em {expires_str}"
                totals["expirados"] += 1
            elif trial:
                icon = "🆓"
                status = f"trial até {expires_str}"
                totals["trial"] += 1
            elif days <= 7:
                icon = "⚠️"
                status = f"expira {expires_str} ({days}d)"
                totals["expirando"] += 1
            else:
                icon = "✅"
                status = f"ativo até {expires_str}"
                totals["ativos"] += 1

            cancelled_tag = " [cancelado]" if cancelled and active else ""
            lines.append(f"{icon} {name} | {number} | {status}{cancelled_tag}")

        total = len(users)
        header = (
            f"👥 *Usuários ({total} total)*\n"
            f"✅ {totals['ativos']} ativos · ⚠️ {totals['expirando']} expirando · "
            f"❌ {totals['expirados']} expirados · 🆓 {totals['trial']} trial · "
            f"⛔ {totals['bloqueados']} bloqueados\n"
            f"{'─' * 30}"
        )

        # Envia em blocos de 30 para não estourar o limite do WhatsApp
        chunk_size = 30
        for i in range(0, len(lines), chunk_size):
            chunk = lines[i:i + chunk_size]
            prefix = header if i == 0 else f"_(continuação {i // chunk_size + 1})_"
            await evolution_client.send_text(sender, prefix + "\n" + "\n".join(chunk))

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

        if state == ConversationState.CANCELLING_SUBSCRIPTION:
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, f"Ok! Assinatura mantida.\n\n{_MENU}")
            return

        if state == ConversationState.SUBSCRIPTION_MENU:
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, _MENU)
            return

        if state == ConversationState.AWAITING_VALUE:
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, f"Ok!\n\n{_MENU}")
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

    async def _handle_idle(self, sender, user, conv, text, is_new_user: bool = False) -> None:
        is_configured = await self._users.user_is_configured(user.id)

        if not is_configured:
            if is_new_user:
                await self._sessions.transition(conv, ConversationState.ONBOARDING_WELCOME)
                if settings.tutorial_video_url:
                    try:
                        await evolution_client.send_video(
                            sender,
                            settings.tutorial_video_url,
                            caption="👆 Assista antes de começar — leva menos de 2 minutos!",
                        )
                    except Exception as exc:
                        logger.warning("Falha ao enviar vídeo tutorial para {}: {}", sender, exc)
                await evolution_client.send_text(
                    sender,
                    f"👋 Olá! Bem-vindo ao *Bot NFSe*!\n\n"
                    f"Sou um assistente que emite sua Nota Fiscal de Serviço (NFS-e) "
                    f"automaticamente pelo portal Emissor Nacional.\n\n"
                    f"✅ *O que faço por você:*\n"
                    f"• Emito sua NFS-e toda semana sem você precisar entrar no portal\n"
                    f"• Aviso toda segunda-feira para você informar o valor\n"
                    f"• Guardo o histórico completo de todas as suas notas\n"
                    f"• Você só informa o valor — eu preencho e envio tudo\n\n"
                    f"🎁 *{settings.trial_days} dias grátis* para você testar, sem precisar de cartão.\n\n"
                    f"Quer começar o cadastro? Responda *SIM*",
                )
            else:
                await self._sessions.transition(conv, ConversationState.ONBOARDING_NAME)
                await evolution_client.send_text(sender, _ONBOARDING_WELCOME)
            return

        text_upper = _normalize(text)
        name = user.name or "você"

        if text_upper in ("1", "EMITIR", "NOTA", "NFSE"):
            await self._ask_for_value(sender, conv)
            return

        if text_upper == "2":
            await self._send_history(sender, user)
            return

        if text_upper == "3":
            await self._sessions.transition(conv, ConversationState.ONBOARDING_USERNAME)
            await evolution_client.send_text(sender, _with_back("Informe seu CNPJ de login:"))
            return

        if text_upper == "4":
            await evolution_client.send_text(sender, f"Falar com suporte:\n{self._admin_wa_link()}")
            return

        if text_upper == "5":
            await self._sessions.transition(conv, ConversationState.SUBSCRIPTION_MENU)
            await evolution_client.send_text(sender, _with_back(_SUBSCRIPTION_SUBMENU))
            return

        if text_upper == "6":
            await self._send_total(sender, user)
            return

        if text_upper == "7":
            hour = user.reminder_hour if user.reminder_hour is not None else 9
            minute = user.reminder_minute if user.reminder_minute is not None else 0
            await self._sessions.transition(conv, ConversationState.AWAITING_REMINDER)
            await evolution_client.send_text(
                sender,
                f"Seu lembrete esta configurado para toda segunda-feira as {hour:02d}:{minute:02d}.\n\n"
                "Qual horario deseja? Digite no formato HH:MM\nExemplo: 08:30",
            )
            return

        menu_personalizado = (
            f"{name}, o que deseja?\n\n"
            "1 - Emitir NFS-e\n"
            "2 - Histórico de notas\n"
            "3 - Atualizar dados\n"
            "4 - Suporte\n"
            "5 - Assinaturas\n"
            "6 - Somar notas\n"
            "7 - Configurar horário do lembrete semanal"
        )

        if text_upper in ("MENU", "INICIO", "INÍCIO", "OI", "OLA", "OLÁ"):
            await evolution_client.send_text(sender, menu_personalizado)
            return

        result = await anthropic_client.classify_or_answer(text)
        action = result.get("action", "unknown")

        if action == "menu":
            option = result.get("option", "")
            if option == "1":
                await self._ask_for_value(sender, conv)
            elif option == "2":
                await self._send_history(sender, user)
            elif option == "3":
                await self._sessions.transition(conv, ConversationState.ONBOARDING_USERNAME)
                await evolution_client.send_text(sender, _with_back("Informe seu CNPJ de login:"))
            elif option == "4":
                await evolution_client.send_text(sender, f"Falar com suporte:\n{self._admin_wa_link()}")
            elif option == "5":
                await self._sessions.transition(conv, ConversationState.SUBSCRIPTION_MENU)
                await evolution_client.send_text(sender, _with_back(_SUBSCRIPTION_SUBMENU))
            elif option == "6":
                await self._send_total(sender, user)
            elif option == "7":
                hour = user.reminder_hour if user.reminder_hour is not None else 9
                minute = user.reminder_minute if user.reminder_minute is not None else 0
                await self._sessions.transition(conv, ConversationState.AWAITING_REMINDER)
                await evolution_client.send_text(
                    sender,
                    f"Seu lembrete esta configurado para toda segunda-feira as {hour:02d}:{minute:02d}.\n\n"
                    "Qual horario deseja? Digite no formato HH:MM\nExemplo: 08:30",
                )
            else:
                await evolution_client.send_text(sender, menu_personalizado)
        elif action == "faq":
            await evolution_client.send_text(sender, result.get("answer", ""))
        else:
            await evolution_client.send_text(sender, menu_personalizado)

    async def _ask_for_value(self, sender: str, conv) -> None:
        start, end = previous_week_period()
        period_str = format_period(start, end)
        ctx = {"periodo": period_str}
        await self._sessions.transition(conv, ConversationState.AWAITING_VALUE, ctx)
        await evolution_client.send_text(
            sender,
            _with_back(f"Informe o valor dos ganhos de {period_str} (ex: 697,08):"),
        )

    # ── ONBOARDING ────────────────────────────────────────────────────────────

    async def _handle_onboarding_welcome(self, sender, user, conv, text) -> None:
        upper = _normalize(text)
        if upper in ("SIM", "S", "COMECAR", "INICIAR", "START", "1",
                     "PODE SER", "PODE", "CLARO", "BORA", "VAMOS", "QUERO",
                     "QUERO SIM", "OK", "TOPO", "VAI", "VAMO"):
            await self._sessions.transition(conv, ConversationState.ONBOARDING_NAME)
            await evolution_client.send_text(sender, "Ótimo! Vamos começar. 😊\n\n" + _with_back("Qual é o seu nome completo?"))
        elif upper in ("NAO", "NAO QUERO", "N", "CANCELAR", "AGORA NAO", "AGORA NÃO", "DEPOIS", "OBRIGADO NAO"):
            await self._sessions.reset(conv)
            await evolution_client.send_text(
                sender,
                "Tudo bem! Se mudar de ideia é só mandar uma mensagem aqui. 👋",
            )
        else:
            result = await anthropic_client.classify_or_answer(text)
            action = result.get("action", "unknown")
            if action == "onboarding_yes":
                await self._sessions.transition(conv, ConversationState.ONBOARDING_NAME)
                await evolution_client.send_text(sender, "Ótimo! Vamos começar. 😊\n\n" + _with_back("Qual é o seu nome completo?"))
            elif action == "onboarding_no":
                await self._sessions.reset(conv)
                await evolution_client.send_text(
                    sender,
                    "Tudo bem! Se mudar de ideia é só mandar uma mensagem aqui. 👋",
                )
            elif action == "faq":
                await evolution_client.send_text(sender, result.get("answer", ""))
                await evolution_client.send_text(
                    sender,
                    "Responda *SIM* para começar o cadastro ou *NÃO* para cancelar.",
                )
            else:
                await evolution_client.send_text(
                    sender,
                    "Responda *SIM* para começar o cadastro ou *NÃO* para cancelar.",
                )

    async def _handle_onboarding_name(self, sender, user, conv, text) -> None:
        if not text:
            await evolution_client.send_text(sender, _with_back("Por favor, informe seu nome."))
            return
        if await self._faq_intercept(sender, text, _with_back("Qual é o seu nome completo?")):
            return
        user.name = text
        ctx = {"nome": text}
        await self._sessions.transition(conv, ConversationState.ONBOARDING_USERNAME, ctx)
        await evolution_client.send_text(
            sender,
            "⚠️ *Atenção antes de continuar!*\n\n"
            "O bot usa login com *CNPJ + Senha* no portal nfse.gov.br.\n\n"
            "Se você só tem acesso pelo *Gov.br*, precisará criar uma senha direta no portal primeiro:\n\n"
            "1️⃣ Acesse nfse.gov.br\n"
            "2️⃣ Clique em *Primeiro acesso*\n"
            "3️⃣ Informe seu CNPJ\n"
            "4️⃣ Crie uma senha\n"
            "5️⃣ Confirme pelo e-mail cadastrado na Receita Federal\n\n"
            "Depois volte aqui e continue o cadastro. 👇",
        )
        await evolution_client.send_text(
            sender,
            _with_back("Informe seu CNPJ de login (somente números):"),
        )

    async def _handle_onboarding_username(self, sender, user, conv, text) -> None:
        if not text or not _validate_cnpj(text):
            if await self._faq_intercept(sender, text, _with_back("Informe seu CNPJ de login (somente números):")):
                return
            await evolution_client.send_text(
                sender,
                _with_back("CNPJ invalido. Informe no formato 00.000.000/0000-00 (14 digitos):"),
            )
            return
        ctx = await self._sessions.get_context(conv)
        ctx["username"] = text
        await self._sessions.transition(conv, ConversationState.ONBOARDING_PASSWORD, ctx)
        await evolution_client.send_text(sender, _with_back("Informe sua senha do portal:"))

    async def _handle_onboarding_password(self, sender, user, conv, text, number, msg_id) -> None:
        if not text:
            await evolution_client.send_text(sender, _with_back("Informe sua senha:"))
            return
        ctx = await self._sessions.get_context(conv)
        ctx["password"] = text
        await self._sessions.transition(conv, ConversationState.ONBOARDING_CITY, ctx)
        await evolution_client.delete_message(number, msg_id)
        await evolution_client.send_text(sender, _with_back("🔒 Senha salva! Apague a mensagem com a senha.\n\nMunicípio (ex: Campinas/SP):"))

    async def _handle_onboarding_city(self, sender, user, conv, text) -> None:
        if not text:
            await evolution_client.send_text(sender, _with_back("Informe seu municipio:"))
            return
        if not re.match(r"^[A-Za-záàâãéèêíïóôõöúçÁÀÂÃÉÈÊÍÏÓÔÕÖÚÇ ]+/[A-Z]{2}$", text.strip()):
            await evolution_client.send_text(
                sender,
                _with_back("Formato invalido. Use Cidade/UF com a sigla do estado em maiúsculas (ex: Campinas/SP):"),
            )
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
            _with_back(
                f"Confirme seus dados:\n\n"
                f"Portal: Emissor Nacional (nfse.gov.br)\n"
                f"Login: {cnpj_fmt}\n"
                f"Senha: ••••••••\n"
                f"CNPJ: {cnpj_fmt}\n"
                f"Municipio: {text}\n\n"
                "Responda SIM para confirmar ou NAO para cancelar."
            ),
        )

    async def _handle_onboarding_confirm(self, sender, user, conv, text) -> None:
        upper = text.upper()
        if upper not in ("SIM", "S", "NAO", "NÃO", "N", "CANCELAR"):
            await evolution_client.send_text(sender, _with_back("Responda SIM para confirmar ou NAO para cancelar."))
            return

        if upper in ("NAO", "NÃO", "N", "CANCELAR"):
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, "Configuracao cancelada. " + _MENU)
            return

        ctx = await self._sessions.get_context(conv)
        is_first_credential = not await self._users.user_is_configured(user.id)

        # Validate credentials before saving — avoids waiting until Monday to discover errors
        await evolution_client.send_text(
            sender,
            "⏳ Verificando suas credenciais no portal... pode levar até 30 segundos.",
        )
        try:
            await self._nfse.test_credentials(ctx["username"], ctx["password"])
        except Exception as exc:
            stage = getattr(exc, "stage", "login")
            msg = await anthropic_client.explain_emission_error(stage, str(exc))
            await evolution_client.send_text(sender, msg)
            await evolution_client.send_text(
                sender,
                _with_back("Corrija suas credenciais e tente novamente.\n\nInforme seu CNPJ de login:"),
            )
            await self._sessions.transition(conv, ConversationState.ONBOARDING_USERNAME, ctx)
            return

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

        if is_first_credential:
            await self._users.activate_trial(user)
            expires = user.subscription_expires_at
            expires_str = expires.strftime("%d/%m/%Y") if expires else "?"

            from app.integrations.mercadopago.client import mp_client
            try:
                url = await asyncio.to_thread(mp_client.create_preference, user.id, sender, user.affiliate_code)
                payment_line = (
                    f"Quer garantir sua assinatura agora? R$ {settings.subscription_price:.2f}/mês:\n"
                    f"👉 {url}\n\n"
                )
            except Exception:
                payment_line = (
                    f"Após esse período, a assinatura é R$ {settings.subscription_price:.2f}/mês.\n"
                    "Digite ASSINAR quando quiser assinar.\n\n"
                )

            await evolution_client.send_text(
                sender,
                f"✅ Tudo pronto! Você tem *{settings.trial_days} dias grátis* — acesso até {expires_str}.\n\n"
                f"{payment_line}"
                "Envie *1* para emitir sua primeira NFS-e.\n\n"
                "💡 Toda segunda-feira vou te lembrar de registrar seus ganhos da semana.\n"
                "Para ajustar o horário do lembrete, envie *HORARIO* ou *7* no menu.",
            )
        else:
            await evolution_client.send_text(sender, "✅ Dados atualizados! Envie 1 para emitir.")

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
                _with_back("Valor inválido. Ex: 697,08"),
            )
            return

        ctx = await self._sessions.get_context(conv)
        period_str = ctx.get("periodo") or format_period(*previous_week_period())
        ctx.update({"valor": value, "periodo": period_str})
        await self._sessions.transition(conv, ConversationState.AWAITING_CONFIRMATION, ctx)

        valor_fmt = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        await evolution_client.send_text(
            sender,
            _with_back(f"Emitir R$ {valor_fmt} — {period_str}? SIM para confirmar."),
        )

    # ── VALUE CONFIRMATION ───────────────────────────────────────────────────

    async def _handle_value_confirm(self, sender, user, conv, text) -> None:
        upper = text.upper()
        if upper not in ("SIM", "S", "NAO", "NÃO", "N", "CANCELAR"):
            await evolution_client.send_text(sender, _with_back("Responda SIM para confirmar ou NAO para cancelar."))
            return

        if upper in ("NAO", "NÃO", "N", "CANCELAR"):
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, "Emissao cancelada. " + _MENU)
            return

        ctx = await self._sessions.get_context(conv)
        value: float = ctx.get("valor", 0)
        period: str = ctx.get("periodo", "")

        valor_br = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        await self._sessions.transition(conv, ConversationState.PROCESSING)
        await evolution_client.send_text(
            sender,
            f"Iniciando emissao de R$ {valor_br}...\nIsso pode levar 1-2 minutos.",
        )

        try:
            invoice = await self._nfse.emit(user.id, value, period)
        except (CredentialNotFoundError, NfseEmissionError) as exc:
            logger.error("Emission error for {}: {}", sender, exc)
            await self._sessions.reset(conv)
            stage = getattr(exc, "stage", "login" if isinstance(exc, CredentialNotFoundError) else "unknown")
            msg = await anthropic_client.explain_emission_error(stage, str(exc))
            await evolution_client.send_text(sender, msg)
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

        year_invoices = [inv for inv in all_invoices if inv.created_at.year == target_year]

        monthly: dict[int, tuple[float, int]] = {}
        for inv in year_invoices:
            m = inv.created_at.month
            total_m, count_m = monthly.get(m, (0.0, 0))
            monthly[m] = (total_m + float(inv.value), count_m + 1)

        total_year = sum(v for v, _ in monthly.values())
        count_year = sum(c for _, c in monthly.values())

        month_section = ""
        if target_year == now.year:
            m_val, m_cnt = monthly.get(now.month, (0.0, 0))
            mes_nome = _MESES_FULL[now.month]
            month_section = (
                f"📅 Total do mês — {mes_nome.capitalize()}/{now.year}:\n"
                f"   {fmt(m_val)} — {m_cnt} nota{'s' if m_cnt != 1 else ''}\n\n"
            )

        breakdown_lines = []
        for m in sorted(monthly):
            v, c = monthly[m]
            breakdown_lines.append(f"   {_MESES[m]}/{str(target_year)[2:]}: {fmt(v)} ({c})")
        breakdown = "\n".join(breakdown_lines)

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

    # ── SUBSCRIPTION MENU ────────────────────────────────────────────────────

    async def _handle_subscription_menu(self, sender, user, conv, text) -> None:
        upper = text.upper()
        if upper in ("1", "ASSINAR", "RENOVAR"):
            await self._sessions.reset(conv)
            await self._send_subscription_link(sender, user)
        elif upper in ("2", "CANCELAR ASSINATURA", "CANCELAR"):
            await self._start_cancellation(sender, user, conv)
        else:
            await evolution_client.send_text(sender, _with_back(_SUBSCRIPTION_SUBMENU))

    # ── SUBSCRIPTION CANCELLATION ────────────────────────────────────────────

    async def _start_cancellation(self, sender: str, user, conv) -> None:
        if not UserService.subscription_active(user):
            await evolution_client.send_text(
                sender,
                "Você não possui assinatura ativa.\n\nDigite ASSINAR para reativar.",
            )
            return

        if UserService.is_cancelled(user):
            expires = user.subscription_expires_at
            expires_str = expires.strftime("%d/%m/%Y") if expires else "?"
            await evolution_client.send_text(
                sender,
                f"Sua assinatura já está cancelada.\nVocê tem acesso até {expires_str}.\n\nDigite ASSINAR para reativar.",
            )
            return

        expires = user.subscription_expires_at
        expires_str = expires.strftime("%d/%m/%Y") if expires else "?"

        await self._sessions.transition(conv, ConversationState.CANCELLING_SUBSCRIPTION)
        await evolution_client.send_text(
            sender,
            _with_back(f"⚠️ Cancelar assinatura? Acesso vai até {expires_str}.\n\nSIM para confirmar."),
        )

    async def _handle_cancellation_confirm(self, sender, user, conv, text) -> None:
        upper = text.upper()
        if upper not in ("SIM", "S", "NAO", "NÃO", "N", "CANCELAR"):
            await evolution_client.send_text(
                sender,
                _with_back("Responda SIM para confirmar o cancelamento ou NAO para manter a assinatura."),
            )
            return

        if upper in ("NAO", "NÃO", "N", "CANCELAR"):
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, f"Ok! Sua assinatura continua ativa.\n\n{_MENU}")
            return

        await self._users.cancel_subscription(user)
        await self._sessions.reset(conv)

        expires = user.subscription_expires_at
        expires_str = expires.strftime("%d/%m/%Y") if expires else "?"

        await evolution_client.send_text(
            sender,
            f"✅ Assinatura cancelada.\n"
            f"Você ainda tem acesso até {expires_str}.\n\n"
            "Se mudar de ideia, digite ASSINAR para reativar.",
        )

    def _admin_wa_link(self) -> str:
        number = re.sub(r"\D", "", settings.admin_number)
        return f"https://wa.me/{number}" if number else "https://wa.me/5519971721948"

    async def _send_subscription_link(self, sender: str, user) -> None:
        """Send payment links: one-time (auto-activate) + recurring plan (manual activate)."""
        from app.integrations.mercadopago.client import mp_client
        try:
            avulso_url = await asyncio.to_thread(mp_client.create_preference, user.id, sender, user.affiliate_code)
        except Exception as exc:
            logger.warning("MP preference creation failed for {}: {}", sender, exc)
            avulso_url = None

        expires = user.subscription_expires_at
        context_line = ""
        if expires and UserService.subscription_active(user):
            context_line = f"Sua assinatura atual vai até {expires.strftime('%d/%m/%Y')}. Assinar agora estende esse prazo.\n\n"

        msg = f"🔑 Assinar Bot NFSe — R$ {settings.subscription_price:.2f}/mês\n\n{context_line}"

        if avulso_url:
            msg += f"💳 *Pagamento avulso* (ativação automática):\n👉 {avulso_url}\n\n"

        if settings.mercadopago_plan_url:
            msg += (
                f"🔄 *Assinatura recorrente* (ativação manual):\n"
                f"👉 {settings.mercadopago_plan_url}\n"
                f"Após assinar, entre em contato para ativar:\n"
                f"👉 {self._admin_wa_link()}\n"
            )

        if not avulso_url and not settings.mercadopago_plan_url:
            msg += f"👉 {self._admin_wa_link()}\n"

        await evolution_client.send_text(sender, msg)

    # ── SUBSCRIPTION EXPIRED ─────────────────────────────────────────────────

    async def _send_subscription_expired(self, sender: str, user) -> None:
        from app.integrations.mercadopago.client import mp_client
        try:
            avulso_url = await asyncio.to_thread(mp_client.create_preference, user.id, sender, user.affiliate_code)
        except Exception as exc:
            logger.warning("MP preference creation failed for {}: {}", sender, exc)
            avulso_url = None

        msg = f"⚠️ Seu acesso expirou!\n\nPara continuar usando o bot — R$ {settings.subscription_price:.2f}/mês:\n\n"

        if avulso_url:
            msg += f"💳 *Pagamento avulso* (ativação automática):\n👉 {avulso_url}\n\n"

        if settings.mercadopago_plan_url:
            msg += (
                f"🔄 *Assinatura recorrente* (ativação manual):\n"
                f"👉 {settings.mercadopago_plan_url}\n"
                f"Após assinar, entre em contato para ativar:\n"
                f"👉 {self._admin_wa_link()}\n\n"
            )

        if not avulso_url and not settings.mercadopago_plan_url:
            msg += f"Dúvidas? Fale com o suporte: {self._admin_wa_link()}\n\n"

        await evolution_client.send_text(sender, msg.rstrip())

    async def _handle_refund_request(self, sender: str, user) -> None:
        """Notifies admin of refund request and confirms to user."""
        admin = re.sub(r"\D", "", settings.admin_number)
        user_name = getattr(user, "name", None) or sender

        if admin:
            admin_jid = f"{admin}@s.whatsapp.net"
            await evolution_client.send_text(
                admin_jid,
                f"🔴 *Solicitação de reembolso*\n\n"
                f"Usuário: {user_name}\n"
                f"WhatsApp: {sender}\n\n"
                f"Entre em contato para verificar.",
            )
            logger.info("Refund request from {} forwarded to admin {}", sender, admin)

        await evolution_client.send_text(
            sender,
            "✅ Solicitação de reembolso enviada!\n\n"
            "Nossa equipe entrará em contato em breve para verificar seu pagamento.\n\n"
            f"Ou fale diretamente pelo suporte:\n👉 {self._admin_wa_link()}",
        )

    async def _handle_set_reminder(self, sender: str, user, conv, text: str) -> None:
        match = re.match(r"HORARIO\s+(\d{1,2}):(\d{2})", text.upper())
        if not match:
            # Bare "HORARIO" — ask for the time
            hour = user.reminder_hour if user.reminder_hour is not None else 9
            minute = user.reminder_minute if user.reminder_minute is not None else 0
            await self._sessions.transition(conv, ConversationState.AWAITING_REMINDER)
            await evolution_client.send_text(
                sender,
                f"Seu lembrete esta configurado para toda segunda-feira as {hour:02d}:{minute:02d}.\n\n"
                "Qual horario deseja? Digite no formato HH:MM\nExemplo: 08:30",
            )
            return

        hour = int(match.group(1))
        minute = int(match.group(2))
        await self._save_reminder(sender, user, hour, minute)

    async def _handle_awaiting_reminder(self, sender: str, user, conv, text: str) -> None:
        match = re.match(r"(\d{1,2}):(\d{2})", text.strip())
        if not match:
            upper = _normalize(text)
            keep_words = ("MANTER", "PODE MANTER", "DEIXA", "MANTEM", "MESMO",
                          "ESSE MESMO", "PODE SER", "OK", "TUDO BEM", "ASSIM",
                          "DEIXA ASSIM", "PODE DEIXAR", "CONTINUA")
            if any(upper == w or upper.startswith(w + " ") for w in keep_words):
                await self._sessions.reset(conv)
                hour = user.reminder_hour if user.reminder_hour is not None else 9
                minute = user.reminder_minute if user.reminder_minute is not None else 0
                await evolution_client.send_text(
                    sender,
                    f"Certo! Lembrete mantido para toda segunda-feira as {hour:02d}:{minute:02d}. ✅",
                )
                return
            await evolution_client.send_text(
                sender,
                "Formato invalido. Digite apenas o horario no formato HH:MM\nExemplo: 08:30",
            )
            return

        hour = int(match.group(1))
        minute = int(match.group(2))
        await self._sessions.reset(conv)
        await self._save_reminder(sender, user, hour, minute)

    async def _save_reminder(self, sender: str, user, hour: int, minute: int) -> None:
        if hour < 6 or hour > 22 or minute > 59:
            await evolution_client.send_text(
                sender,
                "Horario invalido. Escolha entre 06:00 e 22:00.",
            )
            return

        minute = round(minute / 5) * 5
        if minute == 60:
            hour += 1
            minute = 0

        user.reminder_hour = hour
        user.reminder_minute = minute
        await self._session.commit()

        await evolution_client.send_text(
            sender,
            f"Horario atualizado! Voce sera lembrado toda segunda-feira as {hour:02d}:{minute:02d}.",
        )

    async def _handle_payment_receipt(self, sender: str, user, payment_id: str) -> None:
        """User sent a numeric payment ID from their MP receipt. Verify and activate."""
        from app.integrations.mercadopago.client import mp_client

        await evolution_client.send_text(sender, "🔍 Verificando seu pagamento...")

        try:
            payment = await asyncio.to_thread(mp_client.get_payment, payment_id)
        except Exception as exc:
            logger.warning("Receipt check failed for {} payment {}: {}", sender, payment_id, exc)
            await evolution_client.send_text(
                sender,
                "❌ Não consegui verificar esse número de pagamento.\n\n"
                "Verifique se digitou corretamente ou envie *ASSINEI* para ativarmos manualmente.",
            )
            return

        status = payment.get("status", "")
        if status != "approved":
            await evolution_client.send_text(
                sender,
                f"⚠️ Esse pagamento ainda não foi aprovado (status: {status}).\n\n"
                "Aguarde a confirmação do Mercado Pago e tente novamente.",
            )
            return

        if user.last_payment_id == payment_id:
            expires = user.subscription_expires_at
            expires_str = expires.strftime("%d/%m/%Y") if expires else "?"
            await evolution_client.send_text(
                sender,
                f"✅ Esse pagamento já foi processado.\n"
                f"Sua assinatura está ativa até {expires_str}.\n\n"
                "Digite 1 para emitir sua NFS-e agora!",
            )
            return

        await self._users.activate_subscription(user, settings.subscription_days)
        user.subscription_cancelled_at = None
        user.last_payment_id = payment_id
        expires = user.subscription_expires_at
        expires_str = expires.strftime("%d/%m/%Y") if expires else "?"

        await evolution_client.send_text(
            sender,
            f"✅ Pagamento verificado e confirmado!\n"
            f"Sua assinatura está ativa até {expires_str}.\n\n"
            "Digite 1 para emitir sua NFS-e agora!",
        )
        logger.info("Subscription activated via receipt for user {} — payment {}", user.id, payment_id)
