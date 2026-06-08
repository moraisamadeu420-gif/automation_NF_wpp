"""
tests/test_bot_flows.py
Automated tests for the NFSe Bot WhatsApp conversation flows.

Uses in-memory SQLite (StaticPool) and mocked Evolution API.
All app settings are overridden via os.environ before any app import.
"""
# ── Environment overrides — MUST come before any app import ──────────────────
import os
from cryptography.fernet import Fernet

_TEST_KEY = Fernet.generate_key().decode()

os.environ.update({
    "DATABASE_URL":              "sqlite+aiosqlite:///:memory:",
    "DRY_RUN":                   "true",
    "ENCRYPTION_KEY":            _TEST_KEY,
    "ADMIN_NUMBER":              "5500000000000",
    "TRIAL_DAYS":                "15",
    "SUBSCRIPTION_PRICE":       "19.90",
    "SUBSCRIPTION_DAYS":        "30",
    "MERCADOPAGO_ACCESS_TOKEN": "",
    "EVOLUTION_URL":             "http://fake-evo",
    "EVOLUTION_API_KEY":         "test-key",
    "EVOLUTION_INSTANCE":       "test",
})

# Invalidate settings cache if already loaded in a previous test run
try:
    from app.core.config import get_settings
    get_settings.cache_clear()
except ImportError:
    pass

# ── Imports ───────────────────────────────────────────────────────────────────
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.connection import Base
from app.integrations.anthropic.client import anthropic_client
from app.integrations.evolution.client import evolution_client
from app.integrations.evolution.schemas import WebhookMessage
from app.models.session import ConversationState
from app.models.user import User
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.services.message_processor import MessageProcessor
from app.services.nfse_service import NfseService
from app.services.user_service import UserService

# ── In-memory test database ───────────────────────────────────────────────────

_TEST_ENGINE = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)
_TestSession = async_sessionmaker(
    bind=_TEST_ENGINE,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_anthropic():
    """Evita chamadas reais à API Anthropic em testes.
    Retorna onboarding_yes por padrão (classifica qualquer texto como confirmação de onboarding).
    Testes que precisam de outro comportamento podem sobrescrever com patch local."""
    with patch.object(
        anthropic_client,
        "classify_or_answer",
        new_callable=AsyncMock,
        return_value={"action": "onboarding_yes"},
    ):
        yield


@pytest_asyncio.fixture(autouse=True)
async def _reset_db() -> AsyncGenerator[None, None]:
    """Drop and recreate all tables before each test."""
    async with _TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with _TestSession() as session:
        yield session


@pytest_asyncio.fixture
def bot(db: AsyncSession) -> "BotClient":
    return BotClient(db)


@pytest_asyncio.fixture
def admin(db: AsyncSession) -> "BotClient":
    return BotClient(db, number="5500000000000")


# ── BotClient ─────────────────────────────────────────────────────────────────

class BotClient:
    """Drives MessageProcessor in tests and captures Evolution API replies."""

    def __init__(self, session: AsyncSession, number: str = "5541999000001") -> None:
        self.session = session
        self.number = number
        self._jid = f"{number}@s.whatsapp.net"
        self._counter = 0
        self.replies: list[str] = []

    def _build(self, text: str) -> WebhookMessage:
        self._counter += 1
        return WebhookMessage.model_validate({
            "key": {
                "remoteJid": self._jid,
                "fromMe": False,
                "id": f"MSG-{self._counter:04d}",
            },
            "message": {"conversation": text},
            "messageType": "conversation",
            "pushName": "Usuário Teste",
        })

    async def send(self, text: str) -> list[str]:
        """Process a message and return the bot's text replies."""
        self.replies = []

        async def _capture(number: str, msg: str) -> None:
            self.replies.append(msg)

        processor = MessageProcessor(self.session)
        with (
            patch.object(evolution_client, "send_text", side_effect=_capture),
            patch.object(evolution_client, "send_pdf", new_callable=AsyncMock),
            patch.object(evolution_client, "delete_message", new_callable=AsyncMock),
            patch.object(NfseService, "test_credentials", new_callable=AsyncMock),
        ):
            await processor.process(self._build(text))

        await self.session.flush()
        return list(self.replies)

    async def state(self) -> ConversationState:
        repo = SessionRepository(self.session)
        user = await UserRepository(self.session).get_by_whatsapp_number(self.number)
        if not user:
            return ConversationState.IDLE
        conv = await repo.get_by_user(user.id)
        return ConversationState(conv.state) if conv else ConversationState.IDLE

    async def user(self) -> User | None:
        return await UserRepository(self.session).get_by_whatsapp_number(self.number)

    @property
    def last(self) -> str:
        return self.replies[-1] if self.replies else ""

    @property
    def first(self) -> str:
        return self.replies[0] if self.replies else ""

    def replied_with(self, *fragments: str) -> bool:
        """True if any reply contains all given fragments (case-insensitive)."""
        combined = " ".join(self.replies).lower()
        return all(f.lower() in combined for f in fragments)


# ── Test helpers ──────────────────────────────────────────────────────────────

async def do_onboarding(
    b: BotClient,
    name: str = "João Motorista",
    cnpj: str = "12345678000199",
    password: str = "senha123",
    city: str = "Campinas/SP",
    confirm: str = "SIM",
) -> None:
    """Run through the full onboarding flow."""
    await b.send("oi")
    await b.send(name)
    await b.send(cnpj)
    await b.send(password)
    await b.send(city)
    await b.send(confirm)


def _mock_invoice(number: str = "DRY-RUN-001", pdf_path: str | None = None) -> MagicMock:
    inv = MagicMock()
    inv.invoice_number = number
    inv.pdf_path = pdf_path
    return inv


# ═════════════════════════════════════════════════════════════════════════════
# ONBOARDING
# ═════════════════════════════════════════════════════════════════════════════

class TestOnboarding:

    async def test_complete_flow_state(self, bot: BotClient) -> None:
        """Full onboarding ends at IDLE with trial active."""
        await do_onboarding(bot)
        assert await bot.state() == ConversationState.IDLE

    async def test_complete_flow_user_name(self, bot: BotClient) -> None:
        await do_onboarding(bot)
        user = await bot.user()
        assert user is not None and user.name == "João Motorista"

    async def test_complete_flow_trial_activated(self, bot: BotClient) -> None:
        """Onboarding activates 15-day trial automatically."""
        await do_onboarding(bot)
        user = await bot.user()
        assert user.subscription_expires_at is not None
        delta = user.subscription_expires_at - datetime.now()
        assert 14 <= delta.days <= 16

    async def test_complete_flow_welcome_message(self, bot: BotClient) -> None:
        """Welcome message mentions free trial."""
        await do_onboarding(bot)
        assert bot.replied_with("grátis") or bot.replied_with("GRÁTIS") or "dias" in bot.last.lower()

    # ── CNPJ validation ───────────────────────────────────────────────────────

    async def test_cnpj_with_letters_rejected(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("nao-sou-cnpj")
        assert await bot.state() == ConversationState.ONBOARDING_USERNAME
        assert bot.replied_with("cnpj")

    async def test_cnpj_too_short_rejected(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("123456")
        assert await bot.state() == ConversationState.ONBOARDING_USERNAME

    async def test_cnpj_13_digits_rejected(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("1234567800019")  # 13 digits
        assert await bot.state() == ConversationState.ONBOARDING_USERNAME

    async def test_cnpj_formatted_accepted(self, bot: BotClient) -> None:
        """Formatted CNPJ (XX.XXX.XXX/XXXX-XX) is accepted."""
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12.345.678/0001-99")
        assert await bot.state() == ConversationState.ONBOARDING_PASSWORD

    async def test_cnpj_14_digits_accepted(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        assert await bot.state() == ConversationState.ONBOARDING_PASSWORD

    # ── Password ──────────────────────────────────────────────────────────────

    async def test_empty_password_rejected(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        await bot.send("")
        assert await bot.state() == ConversationState.ONBOARDING_PASSWORD
        assert bot.replied_with("senha")

    async def test_password_advances_to_city(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        await bot.send("senha123")
        assert await bot.state() == ConversationState.ONBOARDING_CITY

    async def test_password_security_tip_in_same_message(self, bot: BotClient) -> None:
        """Security tip and city question come in the same message (correct order)."""
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        await bot.send("senha123")
        assert len(bot.replies) == 1, "Should be exactly one message (tip + city question)"
        assert bot.replied_with("senha") and bot.replied_with("pio")

    # ── City ──────────────────────────────────────────────────────────────────

    async def test_any_city_accepted(self, bot: BotClient) -> None:
        """Any non-empty city text advances to ONBOARDING_CONFIRM."""
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        await bot.send("senha123")
        await bot.send("Cidade Inventada/ZZ")
        assert await bot.state() == ConversationState.ONBOARDING_CONFIRM

    # ── Confirm ───────────────────────────────────────────────────────────────

    async def test_confirm_nao_cancels(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        await bot.send("senha123")
        await bot.send("Campinas/SP")
        await bot.send("NAO")
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("cancel")

    async def test_confirm_unexpected_text(self, bot: BotClient) -> None:
        """Unexpected text in ONBOARDING_CONFIRM asks SIM or NAO."""
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        await bot.send("senha123")
        await bot.send("Campinas/SP")
        await bot.send("talvez")
        assert await bot.state() == ConversationState.ONBOARDING_CONFIRM
        assert bot.replied_with("sim") and bot.replied_with("nao")

    # ── Navigation ────────────────────────────────────────────────────────────

    async def test_cancel_mid_onboarding(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        await bot.send("CANCELAR")
        assert await bot.state() == ConversationState.IDLE

    async def test_voltar_from_password_to_username(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        await bot.send("VOLTAR")
        assert await bot.state() == ConversationState.ONBOARDING_USERNAME
        assert bot.replied_with("cnpj")

    async def test_voltar_from_city_to_password(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        await bot.send("senha123")
        await bot.send("VOLTAR")
        assert await bot.state() == ConversationState.ONBOARDING_PASSWORD
        assert bot.replied_with("senha")

    async def test_voltar_from_confirm_to_city(self, bot: BotClient) -> None:
        await bot.send("oi")
        await bot.send("João")
        await bot.send("12345678000199")
        await bot.send("senha123")
        await bot.send("Campinas/SP")
        await bot.send("VOLTAR")
        assert await bot.state() == ConversationState.ONBOARDING_CITY
        assert bot.replied_with("municipio")

    # ── Idle without config ───────────────────────────────────────────────────

    async def test_any_message_from_unconfigured_starts_onboarding(self, bot: BotClient) -> None:
        await bot.send("uma mensagem completamente aleatória!")
        assert await bot.state() == ConversationState.ONBOARDING_WELCOME


# ═════════════════════════════════════════════════════════════════════════════
# EMISSION
# ═════════════════════════════════════════════════════════════════════════════

class TestEmission:

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(self, bot: BotClient, db: AsyncSession) -> None:
        """Onboard user and give active subscription before each emission test."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() + timedelta(days=30)
        await db.flush()

    # ── Value parsing ─────────────────────────────────────────────────────────

    async def test_value_comma_format(self, bot: BotClient) -> None:
        """697,08 (Brazilian format) is accepted."""
        await bot.send("1")
        await bot.send("697,08")
        assert await bot.state() == ConversationState.AWAITING_CONFIRMATION
        assert "697" in bot.last

    async def test_value_dot_format(self, bot: BotClient) -> None:
        """697.08 (international format) is also accepted."""
        await bot.send("1")
        await bot.send("697.08")
        assert await bot.state() == ConversationState.AWAITING_CONFIRMATION

    async def test_value_invalid_text(self, bot: BotClient) -> None:
        await bot.send("1")
        await bot.send("abc")
        assert await bot.state() == ConversationState.AWAITING_VALUE
        assert bot.replied_with("valor")

    async def test_value_zero_acts_as_cancel(self, bot: BotClient) -> None:
        """'0' in AWAITING_VALUE is treated as CANCELAR (resets to IDLE)."""
        await bot.send("1")
        await bot.send("0")
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("cancel")

    async def test_value_negative_stripped_to_positive(self, bot: BotClient) -> None:
        """-100 has the minus sign stripped → treated as 100 (not blocked)."""
        await bot.send("1")
        await bot.send("-100")
        # regex strips non-numeric chars: "-100" → "100" → valid
        assert await bot.state() == ConversationState.AWAITING_CONFIRMATION
        assert "100" in bot.last

    # ── Confirm / Cancel ──────────────────────────────────────────────────────

    @pytest.mark.parametrize("word", ["SIM", "Sim", "sim", "S", "s"])
    async def test_confirm_variations(self, bot: BotClient, word: str) -> None:
        """All SIM variations complete the emission."""
        with patch.object(NfseService, "emit", new_callable=AsyncMock) as mock:
            mock.return_value = _mock_invoice()
            await bot.send("1")
            await bot.send("697,08")
            await bot.send(word)
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("sucesso") or bot.replied_with("NFS-e")

    @pytest.mark.parametrize("word", ["NÃO", "nao", "NAO", "N", "n", "CANCELAR"])
    async def test_cancel_variations(self, bot: BotClient, word: str) -> None:
        """All cancel variations abort emission."""
        await bot.send("1")
        await bot.send("697,08")
        await bot.send(word)
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("cancel")

    async def test_unexpected_response_at_confirmation(self, bot: BotClient) -> None:
        """Unexpected text in AWAITING_CONFIRMATION asks SIM or NAO."""
        await bot.send("1")
        await bot.send("697,08")
        await bot.send("talvez")
        assert await bot.state() == ConversationState.AWAITING_CONFIRMATION
        assert bot.replied_with("sim") and bot.replied_with("nao")

    # ── Full flow ─────────────────────────────────────────────────────────────

    async def test_full_emission_success_message(self, bot: BotClient) -> None:
        with patch.object(NfseService, "emit", new_callable=AsyncMock) as mock:
            mock.return_value = _mock_invoice("NF-2026-001")
            await bot.send("1")
            await bot.send("697,08")
            await bot.send("SIM")
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("NF-2026-001") or bot.replied_with("sucesso")

    async def test_emission_starts_with_option_1(self, bot: BotClient) -> None:
        await bot.send("1")
        assert await bot.state() == ConversationState.AWAITING_VALUE

    async def test_emission_starts_with_iniciar(self, bot: BotClient) -> None:
        await bot.send("INICIAR")
        assert await bot.state() == ConversationState.AWAITING_VALUE

    async def test_voltar_from_confirmation_to_value(self, bot: BotClient) -> None:
        await bot.send("1")
        await bot.send("697,08")
        await bot.send("VOLTAR")
        assert await bot.state() == ConversationState.AWAITING_VALUE


# ═════════════════════════════════════════════════════════════════════════════
# RECONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

class TestReconfiguration:

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(self, bot: BotClient, db: AsyncSession) -> None:
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() + timedelta(days=30)
        await db.flush()

    async def test_reconfig_complete_flow(self, bot: BotClient) -> None:
        await bot.send("3")
        await bot.send("98765432000188")
        await bot.send("nova_senha")
        await bot.send("São Paulo/SP")
        await bot.send("SIM")
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("atualiz") or bot.replied_with("sucesso")

    async def test_reconfig_cancel_mid_flow(self, bot: BotClient) -> None:
        await bot.send("3")
        await bot.send("98765432000188")
        await bot.send("CANCELAR")
        assert await bot.state() == ConversationState.IDLE

    async def test_reconfig_confirm_sim(self, bot: BotClient) -> None:
        await bot.send("3")
        await bot.send("98765432000188")
        await bot.send("nova_senha")
        await bot.send("Rio de Janeiro/RJ")
        await bot.send("SIM")
        assert await bot.state() == ConversationState.IDLE

    async def test_reconfig_cancel_nao(self, bot: BotClient) -> None:
        await bot.send("3")
        await bot.send("98765432000188")
        await bot.send("nova_senha")
        await bot.send("Rio de Janeiro/RJ")
        await bot.send("NAO")
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("cancel")

    async def test_reconfig_does_not_give_new_trial(self, bot: BotClient, db: AsyncSession) -> None:
        """Updating credentials (not first time) does not reset subscription."""
        user = await bot.user()
        original_expires = user.subscription_expires_at

        await bot.send("3")
        await bot.send("98765432000188")
        await bot.send("nova_senha")
        await bot.send("São Paulo/SP")
        await bot.send("SIM")

        await db.refresh(user)
        assert user.subscription_expires_at == original_expires

    async def test_reconfig_invalid_cnpj_rejected(self, bot: BotClient) -> None:
        await bot.send("3")
        await bot.send("nao-e-um-cnpj")
        assert await bot.state() == ConversationState.ONBOARDING_USERNAME


# ═════════════════════════════════════════════════════════════════════════════
# GLOBAL COMMANDS
# ═════════════════════════════════════════════════════════════════════════════

class TestGlobalCommands:

    @pytest_asyncio.fixture(autouse=True)
    async def _setup(self, bot: BotClient, db: AsyncSession) -> None:
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() + timedelta(days=30)
        await db.flush()

    # ── MENU ──────────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("cmd", ["MENU", "INICIO", "INÍCIO"])
    async def test_menu_shows_options(self, bot: BotClient, cmd: str) -> None:
        await bot.send("1")  # go to AWAITING_VALUE
        await bot.send(cmd)
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("emitir") or "1 -" in bot.last

    async def test_menu_from_onboarding_mid_flow(self, bot: BotClient) -> None:
        """MENU resets from any non-IDLE state."""
        await bot.send("3")  # start reconfig
        await bot.send("MENU")
        assert await bot.state() == ConversationState.IDLE

    # ── CANCELAR ──────────────────────────────────────────────────────────────

    async def test_cancelar_during_emission(self, bot: BotClient) -> None:
        await bot.send("1")
        await bot.send("CANCELAR")
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("cancelad")

    async def test_cancelar_at_idle_falls_through(self, bot: BotClient) -> None:
        """CANCELAR at IDLE (no op in progress) → 'Não entendi' fallback."""
        await bot.send("CANCELAR")
        assert await bot.state() == ConversationState.IDLE
        assert len(bot.replies) > 0  # bot must respond

    # ── AJUDA ─────────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("cmd", ["AJUDA", "HELP"])
    async def test_ajuda_shows_help(self, bot: BotClient, cmd: str) -> None:
        replies = await bot.send(cmd)
        assert bot.replied_with("iniciar") or bot.replied_with("comando")

    async def test_ajuda_available_during_emission(self, bot: BotClient) -> None:
        await bot.send("1")
        await bot.send("AJUDA")
        assert await bot.state() == ConversationState.AWAITING_VALUE  # state preserved

    # ── TOTAL / IR ────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("cmd", ["TOTAL", "SOMAR", "SOMA", "IR"])
    async def test_total_commands_no_invoices(self, bot: BotClient, cmd: str) -> None:
        """With no invoices, bot reports no notes found."""
        replies = await bot.send(cmd)
        assert bot.replied_with("nenhuma") or bot.replied_with("nota")

    async def test_total_with_year_parameter(self, bot: BotClient) -> None:
        replies = await bot.send("TOTAL 2025")
        assert len(replies) > 0

    # ── ENCERRAR ──────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("cmd", ["ENCERRAR", "SAIR", "TCHAU"])
    async def test_encerrar_resets_state(self, bot: BotClient, cmd: str) -> None:
        await bot.send("1")
        await bot.send(cmd)
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("logo") or "👋" in bot.last

    # ── INICIAR ───────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("cmd", ["INICIAR", "START"])
    async def test_iniciar_goes_to_awaiting_value(self, bot: BotClient, cmd: str) -> None:
        replies = await bot.send(cmd)
        assert await bot.state() == ConversationState.AWAITING_VALUE
        assert bot.replied_with("valor")

    # ── Edge cases ────────────────────────────────────────────────────────────

    async def test_empty_message_handled(self, bot: BotClient) -> None:
        """Empty string doesn't crash the bot."""
        replies = await bot.send("")
        assert len(replies) > 0

    async def test_emoji_only_handled(self, bot: BotClient) -> None:
        replies = await bot.send("😊🎉🔥")
        assert len(replies) > 0

    async def test_very_long_message_handled(self, bot: BotClient) -> None:
        """500+ character message doesn't crash the bot."""
        replies = await bot.send("a" * 550)
        assert len(replies) > 0

    async def test_random_number_at_idle(self, bot: BotClient) -> None:
        """Unknown option like '99' shows 'Não entendi' fallback."""
        replies = await bot.send("99")
        assert bot.replied_with("deseja") or bot.replied_with("Emitir")

    async def test_group_message_ignored(self, db: AsyncSession) -> None:
        """Messages from groups (@g.us) are silently ignored."""
        msg = WebhookMessage.model_validate({
            "key": {"remoteJid": "112233@g.us", "fromMe": False, "id": "GRP-001"},
            "message": {"conversation": "oi pessoal"},
            "messageType": "conversation",
        })
        processor = MessageProcessor(db)
        with patch.object(evolution_client, "send_text", new_callable=AsyncMock) as mock:
            await processor.process(msg)
        mock.assert_not_called()

    async def test_from_me_message_ignored(self, db: AsyncSession) -> None:
        """Own messages (fromMe=true) are silently ignored."""
        msg = WebhookMessage.model_validate({
            "key": {"remoteJid": "5541999000001@s.whatsapp.net", "fromMe": True, "id": "OWN-001"},
            "message": {"conversation": "resposta automática"},
            "messageType": "conversation",
        })
        processor = MessageProcessor(db)
        with patch.object(evolution_client, "send_text", new_callable=AsyncMock) as mock:
            await processor.process(msg)
        mock.assert_not_called()

    async def test_message_without_content_ignored(self, db: AsyncSession) -> None:
        """Messages with no message body are silently ignored."""
        msg = WebhookMessage.model_validate({
            "key": {"remoteJid": "5541999000001@s.whatsapp.net", "fromMe": False, "id": "EMPTY-001"},
            "messageType": "conversation",
        })
        processor = MessageProcessor(db)
        with patch.object(evolution_client, "send_text", new_callable=AsyncMock) as mock:
            await processor.process(msg)
        mock.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# SUBSCRIPTION & ADMIN
# ═════════════════════════════════════════════════════════════════════════════

class TestSubscription:

    async def test_expired_trial_blocks_emission(self, bot: BotClient, db: AsyncSession) -> None:
        """User with expired trial cannot emit — sees payment message."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()

        await bot.send("1")
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("expirou") or bot.replied_with("assine") or "⚠️" in bot.last

    async def test_expired_trial_blocks_menu(self, bot: BotClient, db: AsyncSession) -> None:
        """Even MENU is blocked when subscription is expired."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()

        await bot.send("MENU")
        assert bot.replied_with("expirou") or bot.replied_with("assine")

    async def test_legacy_user_none_expiry_not_blocked(self, bot: BotClient, db: AsyncSession) -> None:
        """Users with subscription_expires_at=None (legacy) are not blocked."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = None
        await db.flush()

        await bot.send("1")
        assert await bot.state() == ConversationState.AWAITING_VALUE

    async def test_blocked_user_gets_blocked_message(self, bot: BotClient, db: AsyncSession) -> None:
        await do_onboarding(bot)
        user = await bot.user()
        user.is_blocked = True
        await db.flush()

        await bot.send("1")
        assert bot.replied_with("bloqueada") or "⛔" in bot.last

    async def test_onboarding_states_exempt_from_expiry_check(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Expired users can still complete onboarding (e.g., update credentials)."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()

        # Force into onboarding state directly
        repo = SessionRepository(db)
        conv = await repo.get_by_user(user.id)
        await repo.transition(conv, ConversationState.ONBOARDING_USERNAME)

        # Should proceed normally (not show expiry message)
        await bot.send("12345678000199")
        assert await bot.state() == ConversationState.ONBOARDING_PASSWORD

    # ── Admin: ATIVAR ─────────────────────────────────────────────────────────

    async def test_admin_activates_subscription(
        self, bot: BotClient, admin: BotClient, db: AsyncSession
    ) -> None:
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()

        await admin.send("oi")  # creates admin user in DB
        await admin.send(f"ATIVAR {bot.number} 30")

        assert admin.replied_with("ativada") or "✅" in admin.last
        await db.refresh(user)
        assert user.subscription_expires_at is not None
        assert user.subscription_expires_at > datetime.now()

    async def test_admin_activate_nonexistent_user(self, admin: BotClient) -> None:
        await admin.send("oi")
        await admin.send("ATIVAR 5599999999999 30")
        assert admin.replied_with("encontrado")

    async def test_admin_activate_invalid_days(self, bot: BotClient, admin: BotClient) -> None:
        """ATIVAR with non-numeric days falls back to default subscription_days."""
        await do_onboarding(bot)
        await admin.send("oi")
        await admin.send(f"ATIVAR {bot.number} abc")
        assert admin.replied_with("ativada") or "dias" in admin.last.lower()

    # ── Admin: BLOQUEAR / DESBLOQUEAR ─────────────────────────────────────────

    async def test_admin_blocks_user(
        self, bot: BotClient, admin: BotClient, db: AsyncSession
    ) -> None:
        await do_onboarding(bot)
        await admin.send("oi")
        await admin.send(f"BLOQUEAR {bot.number}")

        user = await bot.user()
        await db.refresh(user)
        assert user.is_blocked is True

    async def test_admin_unblocks_user(
        self, bot: BotClient, admin: BotClient, db: AsyncSession
    ) -> None:
        await do_onboarding(bot)
        user = await bot.user()
        user.is_blocked = True
        await db.flush()

        await admin.send("oi")
        await admin.send(f"DESBLOQUEAR {bot.number}")

        await db.refresh(user)
        assert user.is_blocked is False

    # ── Admin: STATUS ─────────────────────────────────────────────────────────

    async def test_admin_status_active_user(
        self, bot: BotClient, admin: BotClient, db: AsyncSession
    ) -> None:
        await do_onboarding(bot)
        await admin.send("oi")
        await admin.send(f"STATUS {bot.number}")
        assert admin.replied_with("ativo") or admin.replied_with("expira")

    async def test_admin_status_nonexistent(self, admin: BotClient) -> None:
        await admin.send("oi")
        await admin.send("STATUS 5599999999999")
        assert admin.replied_with("encontrado")

    # ── Non-admin cannot use admin commands ───────────────────────────────────

    async def test_non_admin_ativar_ignored(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Regular user typing ATIVAR is treated as idle input, not admin."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() + timedelta(days=30)
        await db.flush()

        await bot.send(f"ATIVAR 5541999000001 30")
        # Not an admin → falls through to _handle_idle → "Não entendi"
        assert bot.replied_with("deseja") or bot.replied_with("Emitir")

    # ── Cancellation ──────────────────────────────────────────────────────────

    async def test_cancel_subscription_flow_confirm(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """User can cancel subscription and receives confirmation."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() + timedelta(days=20)
        await db.flush()

        await bot.send("CANCELAR ASSINATURA")
        assert await bot.state() == ConversationState.CANCELLING_SUBSCRIPTION
        assert bot.replied_with("certeza") or bot.replied_with("cancelar")

        await bot.send("SIM")

        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("cancelada") or "✅" in bot.last

        await db.refresh(user)
        assert user.subscription_cancelled_at is not None

    async def test_cancel_subscription_abort_nao(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """User aborts cancellation with NAO — subscription unchanged."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() + timedelta(days=20)
        await db.flush()

        await bot.send("CANCELAR ASSINATURA")
        await bot.send("NAO")

        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("continua") or bot.replied_with("ativa")

        await db.refresh(user)
        assert user.subscription_cancelled_at is None

    async def test_cancel_already_cancelled(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Already-cancelled user sees info message instead of confirmation."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() + timedelta(days=10)
        user.subscription_cancelled_at = datetime.now()
        await db.flush()

        await bot.send("CANCELAR ASSINATURA")
        assert await bot.state() == ConversationState.IDLE
        assert bot.replied_with("já") or bot.replied_with("cancelada")

    async def test_assinar_shows_payment_link(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """ASSINAR shows a payment link (or support fallback when MP not configured)."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() + timedelta(days=30)
        await db.flush()

        await bot.send("ASSINAR")
        assert bot.replied_with("assinar") or bot.replied_with("suporte") or "wa.me" in bot.last


# ═════════════════════════════════════════════════════════════════════════════
# PAYMENT CONFIRMATION — avulso e recorrente
# ═════════════════════════════════════════════════════════════════════════════

class TestPaymentConfirmation:

    # ── Pagamento avulso (webhook MP) ─────────────────────────────────────────

    async def test_avulso_webhook_ativa_subscription(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """_handle_payment com status approved → ativa subscription e envia confirmação."""
        from unittest.mock import AsyncMock, patch
        from app.api.routes.mercadopago import _handle_payment
        import app.api.routes.mercadopago as mp_route

        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()
        user_id = user.id

        payment_id = "PAY-TEST-AVULSO-001"
        confirmations: list[str] = []

        async def fake_send(number: str, msg: str) -> None:
            confirmations.append(msg)

        with (
            patch("app.integrations.mercadopago.client.mp_client.get_payment", return_value={
                "id": payment_id,
                "status": "approved",
                "external_reference": str(user_id),
                "metadata": {"whatsapp_number": bot.number},
            }),
            patch("app.api.routes.mercadopago.AsyncSessionFactory", _TestSession),
            patch("app.integrations.evolution.client.evolution_client.send_text",
                  side_effect=fake_send),
        ):
            await _handle_payment(payment_id)

        async with _TestSession() as s:
            u = await UserRepository(s).get_by_id(user_id)
            assert u.subscription_expires_at > datetime.now()
            assert u.last_payment_id == payment_id

        assert any("confirmado" in m.lower() or "✅" in m for m in confirmations)

    async def test_avulso_webhook_duplicata_ignorada(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Mesmo payment_id processado duas vezes → segundo é ignorado."""
        from app.api.routes.mercadopago import _handle_payment

        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()
        user_id = user.id

        payment_id = "PAY-TEST-DEDUP-001"
        calls: list[str] = []

        async def fake_send(number: str, msg: str) -> None:
            calls.append(msg)

        with (
            patch("app.integrations.mercadopago.client.mp_client.get_payment", return_value={
                "id": payment_id,
                "status": "approved",
                "external_reference": str(user_id),
                "metadata": {"whatsapp_number": bot.number},
            }),
            patch("app.api.routes.mercadopago.AsyncSessionFactory", _TestSession),
            patch("app.integrations.evolution.client.evolution_client.send_text",
                  side_effect=fake_send),
        ):
            await _handle_payment(payment_id)
            calls.clear()
            await _handle_payment(payment_id)  # duplicata

        # Segunda chamada não deve enviar mensagem
        assert calls == []

    async def test_avulso_webhook_status_pendente_ignorado(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Pagamento com status pending não ativa assinatura."""
        from app.api.routes.mercadopago import _handle_payment

        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()
        user_id = user.id

        calls: list[str] = []

        with (
            patch("app.integrations.mercadopago.client.mp_client.get_payment", return_value={
                "id": "PAY-PENDING-001",
                "status": "pending",
                "external_reference": str(user_id),
                "metadata": {},
            }),
            patch("app.api.routes.mercadopago.AsyncSessionFactory", _TestSession),
            patch("app.integrations.evolution.client.evolution_client.send_text",
                  side_effect=lambda n, m: calls.append(m)),
        ):
            await _handle_payment("PAY-PENDING-001")

        async with _TestSession() as s:
            u = await UserRepository(s).get_by_id(user_id)
            assert not UserService.subscription_active(u)

        assert calls == []

    # ── Comprovante numérico (receipt verification via MP API) ────────────────

    async def test_receipt_ativa_subscription(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Usuário envia ID de pagamento aprovado → assinatura ativada."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()

        payment_id = "12345678901"

        with patch(
            "app.integrations.mercadopago.client.mp_client.get_payment",
            return_value={
                "id": payment_id,
                "status": "approved",
                "external_reference": str(user.id),
                "metadata": {},
            },
        ):
            await bot.send(payment_id)

        await db.refresh(user)
        assert user.subscription_expires_at > datetime.now()
        assert user.last_payment_id == payment_id
        assert bot.replied_with("confirmado") or "✅" in bot.last

    async def test_receipt_status_pendente_nao_ativa(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Pagamento com status pending → informa que ainda não foi aprovado."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()
        original_expires = user.subscription_expires_at

        payment_id = "99887766554"

        with patch(
            "app.integrations.mercadopago.client.mp_client.get_payment",
            return_value={"id": payment_id, "status": "pending"},
        ):
            await bot.send(payment_id)

        await db.refresh(user)
        assert user.subscription_expires_at == original_expires
        assert bot.replied_with("aprovado") or bot.replied_with("status") or "⚠️" in bot.last

    async def test_receipt_api_error_mostra_fallback(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Erro na API do MP → bot pede para usar ASSINEI."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()
        original_expires = user.subscription_expires_at

        payment_id = "11223344556"

        with patch(
            "app.integrations.mercadopago.client.mp_client.get_payment",
            side_effect=RuntimeError("MP API offline"),
        ):
            await bot.send(payment_id)

        await db.refresh(user)
        assert user.subscription_expires_at == original_expires
        assert bot.replied_with("ASSINEI") or bot.replied_with("verificar") or "❌" in bot.last

    async def test_receipt_duplicata_ignorada(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Mesmo ID de pagamento enviado duas vezes → segundo informa que já foi processado."""
        await do_onboarding(bot)
        user = await bot.user()
        user.subscription_expires_at = datetime.now() - timedelta(days=1)
        await db.flush()

        payment_id = "55544433322"

        with patch(
            "app.integrations.mercadopago.client.mp_client.get_payment",
            return_value={
                "id": payment_id,
                "status": "approved",
                "external_reference": str(user.id),
                "metadata": {},
            },
        ):
            await bot.send(payment_id)
            await db.refresh(user)
            first_expires = user.subscription_expires_at

            await bot.send(payment_id)  # duplicata

        await db.refresh(user)
        delta = abs((user.subscription_expires_at - first_expires).total_seconds())
        assert delta < 5  # data não mudou na segunda chamada
        assert bot.replied_with("processado") or bot.replied_with("ativa") or "✅" in bot.last

    async def test_numero_curto_nao_tratado_como_comprovante(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Número com < 8 dígitos não é tratado como comprovante (cai no fluxo normal)."""
        await do_onboarding(bot)

        # "1" é opção de menu, não comprovante — deve mostrar menu/emisssão
        replies = await bot.send("1")

        assert replies  # bot respondeu algo (menu/valor)
        # Não deve ter enviado mensagem de verificação de pagamento
        all_text = " ".join(replies).lower()
        assert "verificando" not in all_text

    async def test_numero_longo_nao_tratado_como_comprovante(
        self, bot: BotClient, db: AsyncSession
    ) -> None:
        """Número com > 13 dígitos não é tratado como comprovante."""
        await do_onboarding(bot)

        replies = await bot.send("12345678901234")  # 14 dígitos

        all_text = " ".join(replies).lower()
        assert "verificando" not in all_text
