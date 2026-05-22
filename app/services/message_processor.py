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
from datetime import datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
    "5 - Cancelar assinatura\n"
    "6 - Somar notas fiscais\n\n"
    "💡 Dica: A qualquer momento você pode digitar:\n"
    "• INICIAR — iniciar emissão de NFS-e\n"
    "• TOTAL — somar todas as notas (útil para o IR)\n"
    "• ASSINAR — renovar / reativar sua assinatura\n"
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
    "5 - Cancelar assinatura\n"
    "6 - Somar notas fiscais\n\n"
    "A qualquer momento:\n"
    "INICIAR — inicia a emissão de NFS-e\n"
    "TOTAL — soma as notas do ano atual\n"
    "TOTAL 2025 — soma as notas de um ano específico\n"
    "ASSINAR — renovar ou reativar sua assinatura\n"
    "CANCELAR ASSINATURA — cancela sua assinatura\n"
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

_ONBOARDING_STATES = {
    ConversationState.ONBOARDING_NAME,
    ConversationState.ONBOARDING_USERNAME,
    ConversationState.ONBOARDING_PASSWORD,
    ConversationState.ONBOARDING_CITY,
    ConversationState.ONBOARDING_CONFIRM,
}

# States where the subscription check is skipped (allow even after expiry)
_SUBSCRIPTION_EXEMPT_STATES = _ONBOARDING_STATES | {ConversationState.CANCELLING_SUBSCRIPTION}


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


def _is_admin(sender: str) -> bool:
    admin = re.sub(r"\D", "", settings.admin_number)
    return bool(admin) and sender == admin


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
        user, is_new_user = await self._users.get_or_create_user(sender, message.push_name)
        conv = await self._sessions.get_or_create(user.id)

        state = ConversationState(conv.state)
        text = (message.message.text or "").strip()

        logger.info("Message from {} | state: {} | text: '{}'", sender, state, text[:80])

        if state == ConversationState.PROCESSING:
            logger.debug("Ignoring message — emission in progress for {}", sender)
            return

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

        text_upper = text.upper()

        # ── Comprovante MP: número puro de 8-13 dígitos ─────────────────────
        # Permite verificar pagamento avulso quando o webhook falha
        _stripped = text.strip().replace(" ", "")
        if _stripped.isdigit() and 8 <= len(_stripped) <= 13:
            await self._handle_payment_receipt(sender, user, _stripped)
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
            await self._handle_idle(sender, user, conv, text, is_new_user)
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

    # ── ADMIN ────────────────────────────────────────────────────────────────

    async def _handle_admin(self, sender: str, text: str) -> bool:
        """Returns True if an admin command was handled."""
        parts = text.split()
        if not parts:
            return False
        cmd = parts[0].upper()

        if cmd == "ATIVAR" and len(parts) >= 3:
            target_number = re.sub(r"\D", "", parts[1])
            days_str = parts[2]
            if not days_str.isdigit():
                await evolution_client.send_text(sender, "Uso: ATIVAR <número> <dias>")
                return True
            days = int(days_str)
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

    async def _handle_idle(self, sender, user, conv, text, is_new_user: bool = False) -> None:
        is_configured = await self._users.user_is_configured(user.id)

        if not is_configured:
            await self._sessions.transition(conv, ConversationState.ONBOARDING_NAME)
            if is_new_user:
                await evolution_client.send_text(
                    sender,
                    f"👋 Olá! Bem-vindo ao Bot NFS-e SPX Driver!\n\n"
                    "Sou seu assistente para emissão automática\n"
                    "de Nota Fiscal de Serviços pelo WhatsApp.\n\n"
                    f"🎁 Você terá {settings.trial_days} dias GRÁTIS para testar!\n\n"
                    "Vamos configurar sua conta. Qual é o seu nome completo?",
                )
            else:
                await evolution_client.send_text(sender, _ONBOARDING_WELCOME)
            return

        text_upper = text.upper()
        name = user.name or "você"

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
            await self._start_cancellation(sender, user, conv)
            return

        if text_upper == "6":
            await self._send_total(sender, user)
            return

        menu_personalizado = (
            f"Olá, {name}! 👋 O que deseja fazer?\n\n"
            "1 - Emitir NFS-e da semana\n"
            "2 - Ver histórico de notas\n"
            "3 - Atualizar meus dados\n"
            "4 - Ajuda\n"
            "5 - Cancelar assinatura\n"
            "6 - Somar notas fiscais\n\n"
            "💡 Dica: A qualquer momento você pode digitar:\n"
            "• INICIAR — iniciar emissão de NFS-e\n"
            "• TOTAL — somar todas as notas (útil para o IR)\n"
            "• ASSINAR — renovar / reativar sua assinatura\n"
            "• CANCELAR — cancelar a operação atual\n"
            "• MENU — voltar ao menu principal"
        )

        if text_upper in ("MENU", "INICIO", "INÍCIO", "OI", "OLA", "OLÁ"):
            await evolution_client.send_text(sender, menu_personalizado)
            return

        await evolution_client.send_text(sender, menu_personalizado)

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

    async def _handle_onboarding_password(self, sender, user, conv, text, number, msg_id) -> None:
        if not text:
            await evolution_client.send_text(sender, "Informe sua senha:")
            return
        ctx = await self._sessions.get_context(conv)
        ctx["password"] = text
        await self._sessions.transition(conv, ConversationState.ONBOARDING_CITY, ctx)
        await evolution_client.delete_message(number, msg_id)
        await evolution_client.send_text(
            sender,
            "🔒 Senha salva!\n"
            "💡 Dica de segurança: apague sua mensagem com a senha do chat agora\n"
            "(segure a mensagem → Apagar → Apagar para todos).\n\n"
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
        if upper not in ("SIM", "S", "NAO", "NÃO", "N", "CANCELAR"):
            await evolution_client.send_text(sender, "Responda SIM para confirmar ou NAO para cancelar.")
            return

        if upper in ("NAO", "NÃO", "N", "CANCELAR"):
            await self._sessions.reset(conv)
            await evolution_client.send_text(sender, "Configuracao cancelada. " + _MENU)
            return

        ctx = await self._sessions.get_context(conv)
        is_first_credential = not await self._users.user_is_configured(user.id)

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
                url = await asyncio.to_thread(mp_client.create_preference, user.id, sender)
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
                f"✅ Configuração salva com sucesso!\n\n"
                f"🎁 Você ganhou {settings.trial_days} dias GRÁTIS para testar o bot!\n"
                f"Seu acesso gratuito expira em: {expires_str}\n\n"
                f"{payment_line}"
                "📅 Toda segunda-feira às 9h você receberá o pedido de valor para emissão automática da NFS-e.\n\n"
                "Ou envie 1 a qualquer momento para emitir agora!",
            )
        else:
            await evolution_client.send_text(
                sender,
                "✅ Dados atualizados com sucesso!\n\n"
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
        if upper not in ("SIM", "S", "NAO", "NÃO", "N", "CANCELAR"):
            await evolution_client.send_text(sender, "Responda SIM para confirmar ou NAO para cancelar.")
            return

        if upper in ("NAO", "NÃO", "N", "CANCELAR"):
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
            f"⚠️ Tem certeza que deseja cancelar sua assinatura?\n\n"
            f"Você perderá o acesso ao bot no dia {expires_str}.\n"
            f"Seus dados ficam salvos por 30 dias caso queira reativar.\n\n"
            "Responda SIM para confirmar o cancelamento.",
        )

    async def _handle_cancellation_confirm(self, sender, user, conv, text) -> None:
        upper = text.upper()
        if upper not in ("SIM", "S", "NAO", "NÃO", "N", "CANCELAR"):
            await evolution_client.send_text(
                sender,
                "Responda SIM para confirmar o cancelamento ou NAO para manter a assinatura.",
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

    async def _send_subscription_link(self, sender: str, user) -> None:
        """Send a fresh payment link for subscription renewal / reactivation."""
        from app.integrations.mercadopago.client import mp_client
        try:
            url = await asyncio.to_thread(mp_client.create_preference, user.id, sender)
        except Exception as exc:
            logger.warning("MP preference creation failed for {}: {}", sender, exc)
            url = "https://wa.me/5519971721948"

        expires = user.subscription_expires_at
        context_line = ""
        if expires and UserService.subscription_active(user):
            context_line = f"Sua assinatura atual vai até {expires.strftime('%d/%m/%Y')}. Assinar agora estende esse prazo.\n\n"

        await evolution_client.send_text(
            sender,
            f"🔑 Assinar Bot NFSe — R$ {settings.subscription_price:.2f}/mês\n\n"
            f"{context_line}"
            f"👉 {url}\n\n"
            "Após o pagamento sua assinatura é ativada automaticamente!",
        )

    # ── SUBSCRIPTION EXPIRED ─────────────────────────────────────────────────

    async def _send_subscription_expired(self, sender: str, user) -> None:
        from app.integrations.mercadopago.client import mp_client
        try:
            url = await asyncio.to_thread(mp_client.create_preference, user.id, sender)
        except Exception as exc:
            logger.warning("MP preference creation failed for {}: {}", sender, exc)
            url = "https://wa.me/5519971721948"

        await evolution_client.send_text(
            sender,
            f"⚠️ Seu acesso expirou!\n\n"
            f"Para continuar usando o bot — R$ {settings.subscription_price:.2f}/mês:\n\n"
            f"👉 {url}\n\n"
            "Dúvidas? Fale com o suporte: https://wa.me/5519971721948",
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
