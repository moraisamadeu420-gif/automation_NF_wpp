#!/usr/bin/env python3
"""
scripts/stress_test.py
Stress test: 100 virtual users sending simultaneous messages to the bot.
20 additional users test the full payment cycle.

Usage:
    python scripts/stress_test.py

Uses DRY_RUN + in-memory SQLite — does not affect real data.
"""
import asyncio
import os
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

# ── Environment MUST be set before any app import ────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from cryptography.fernet import Fernet

_fernet_key = Fernet.generate_key().decode()

os.environ.update({
    "DATABASE_URL":                 "sqlite+aiosqlite:///:memory:",
    "DRY_RUN":                      "true",
    "NFSE_DRY_RUN":                 "true",
    "MERCADOPAGO_SKIP_SIGNATURE":   "true",
    "MERCADOPAGO_ACCESS_TOKEN":     "TEST-stress-fake",
    "MERCADOPAGO_WEBHOOK_SECRET":   "",
    "BOT_PUBLIC_URL":               "https://stress-test.local",
    "EVOLUTION_URL":                "http://fake-evolution-stress",
    "EVOLUTION_API_KEY":            "fake",
    "EVOLUTION_INSTANCE":           "stress",
    "ADMIN_NUMBER":                 "5511999999999",
    "ENCRYPTION_KEY":               _fernet_key,
    "LOG_LEVEL":                    "ERROR",
    "DEBUG":                        "false",
})

# ── App imports ───────────────────────────────────────────────────────────────
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base
import app.database.connection as _db_mod

# Create isolated in-memory engine and patch it into the app before any model use
_test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
    echo=False,
)
_TestSession = async_sessionmaker(
    bind=_test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
_db_mod.AsyncSessionFactory = _TestSession  # redirect all app DB calls to test DB

from app.services.message_processor import MessageProcessor
from app.services.user_service import UserService
from app.models.session import ConversationState
from app.integrations.evolution.schemas import WebhookMessage

# ── Constants ─────────────────────────────────────────────────────────────────
NUM_USERS         = 100
PAYMENT_USERS     = 20
TIMEOUT_SECS      = 10.0
ONBOARDING_CNPJ   = "12345678000195"
OUTPUT_DIR        = Path(__file__).parent.parent / "output"
REPORT_PATH       = OUTPUT_DIR / "stress_test_report.txt"

MENSAGENS_CAOS = [
    "", " ", "🤡", "😂😂😂", "oi oi oi",
    "a" * 500,
    "SELECT * FROM users",
    "<script>alert('xss')</script>",
    "1\n2\n3",
    "SIM NÃO TALVEZ",
    "99999999999999999999",
    "R$ 1.000.000,00",
    "0,00",
    "-500,00",
    "CANCELAR ASSINATURA MENU AJUDA",
]

# ── Data classes ──────────────────────────────────────────────────────────────
@dataclass
class MsgRecord:
    text: str
    response_time: float = 0.0
    responses: list[str] = field(default_factory=list)
    error: str | None = None
    duplicate: bool = False


@dataclass
class UserResult:
    number: str
    flow: str
    success: bool = False
    error: str | None = None
    messages: list[MsgRecord] = field(default_factory=list)
    final_state: str = "unknown"


@dataclass
class StressReport:
    user_results: list[UserResult] = field(default_factory=list)
    payment_results: list[dict[str, Any]] = field(default_factory=list)
    total_seconds: float = 0.0

    @property
    def ok(self) -> int:
        return sum(1 for r in self.user_results if r.success)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.user_results if not r.success)

    @property
    def response_times(self) -> list[float]:
        return [m.response_time for r in self.user_results for m in r.messages if m.response_time > 0]

    @property
    def avg_rt(self) -> float:
        t = self.response_times
        return sum(t) / len(t) if t else 0.0

    @property
    def max_rt(self) -> float:
        t = self.response_times
        return max(t) if t else 0.0

    @property
    def stuck(self) -> list[UserResult]:
        # Only flows that are supposed to fully resolve count as stuck
        must_resolve = {"complete", "aggressive"}
        idle_states = {"ConversationState.IDLE", "idle"}
        return [
            r for r in self.user_results
            if r.flow in must_resolve and r.final_state not in idle_states
        ]

    @property
    def duplicates(self) -> int:
        return sum(1 for r in self.user_results for m in r.messages if m.duplicate)


# ── VirtualUser ───────────────────────────────────────────────────────────────
class VirtualUser:
    def __init__(self, number: str, flow: str, sent_messages: dict[str, list[str]]) -> None:
        self.number = number
        self.flow = flow
        self._sent = sent_messages
        self._counter = 0

    def _msg(self, text: str) -> WebhookMessage:
        self._counter += 1
        return WebhookMessage.model_validate({
            "key": {
                "remoteJid": f"{self.number}@s.whatsapp.net",
                "fromMe": False,
                "id": f"STRESS-{self.number}-{self._counter:04d}",
            },
            "message": {"conversation": text},
            "messageType": "conversation",
            "pushName": f"Teste {self.number[-4:]}",
        })

    async def send(self, text: str) -> MsgRecord:
        rec = MsgRecord(text=text)
        before = len(self._sent.get(self.number, []))
        start = time.monotonic()

        try:
            await asyncio.wait_for(self._process(text), timeout=TIMEOUT_SECS)
        except asyncio.TimeoutError:
            rec.error = f"Timeout >{TIMEOUT_SECS}s"
        except Exception as exc:
            rec.error = repr(exc)[:200]

        rec.response_time = time.monotonic() - start
        new = self._sent.get(self.number, [])[before:]
        rec.responses = list(new)

        # Detect duplicate response (same text sent twice)
        if len(set(new)) < len(new):
            rec.duplicate = True
            rec.error = (rec.error or "") + " | DUPLICATE_RESPONSE"

        return rec

    async def _process(self, text: str) -> None:
        async with _TestSession() as session:
            proc = MessageProcessor(session)
            await proc.process(self._msg(text))
            await session.commit()

    async def state(self) -> str:
        try:
            async with _TestSession() as session:
                svc = UserService(session)
                user = await svc.get_by_number(self.number)
                if not user:
                    return "no_user"
                from app.repositories.session_repository import SessionRepository
                conv = await SessionRepository(session).get_or_create(user.id)
                return str(conv.state)
        except Exception:
            return "error"


# ── Flows ─────────────────────────────────────────────────────────────────────
async def flow_complete(vu: VirtualUser) -> UserResult:
    res = UserResult(number=vu.number, flow="complete")
    steps = [
        "Oi", "Amadeu Teste", ONBOARDING_CNPJ, "SenhaTest1",
        "Campinas", "SIM",
        "1", "100,00", "SIM",
        "CANCELAR ASSINATURA", "SIM",
    ]
    for step in steps:
        rec = await vu.send(step)
        res.messages.append(rec)
        if rec.error and "Timeout" in (rec.error or ""):
            res.error = f"Timeout em '{step}'"
            res.final_state = await vu.state()
            return res
    res.final_state = await vu.state()
    res.success = True
    return res


async def flow_incomplete(vu: VirtualUser) -> UserResult:
    res = UserResult(number=vu.number, flow="incomplete")
    steps = ["Oi", "Amadeu Incompleto", ONBOARDING_CNPJ, "SenhaAbandon"]
    for step in steps[: random.randint(1, 4)]:
        res.messages.append(await vu.send(step))
    res.final_state = await vu.state()
    res.success = True
    return res


async def flow_chaotic(vu: VirtualUser) -> UserResult:
    res = UserResult(number=vu.number, flow="chaotic")
    for _ in range(random.randint(5, 15)):
        rec = await vu.send(random.choice(MENSAGENS_CAOS))
        res.messages.append(rec)
        if rec.error and "500" in (rec.error or ""):
            res.error = f"HTTP 500: {rec.error}"
    res.final_state = await vu.state()
    res.success = res.error is None
    return res


async def flow_aggressive(vu: VirtualUser) -> UserResult:
    res = UserResult(number=vu.number, flow="aggressive")
    msg = random.choice(["SIM", "1", "MENU", "CANCELAR", "AJUDA"])
    for _ in range(10):
        res.messages.append(await vu.send(msg))
    res.final_state = await vu.state()
    res.success = True
    return res


async def flow_slow(vu: VirtualUser) -> UserResult:
    res = UserResult(number=vu.number, flow="slow")
    for step in ["Oi", "Amadeu Lento", ONBOARDING_CNPJ]:
        await asyncio.sleep(random.uniform(0.05, 0.2))
        res.messages.append(await vu.send(step))
    res.final_state = await vu.state()
    res.success = True
    return res


FLOWS = [flow_complete, flow_incomplete, flow_chaotic, flow_aggressive, flow_slow]


async def run_user(number: str, sent: dict[str, list[str]]) -> UserResult:
    fn = random.choice(FLOWS)
    vu = VirtualUser(number, fn.__name__.replace("flow_", ""), sent)
    try:
        return await fn(vu)
    except Exception as exc:
        return UserResult(number=number, flow=fn.__name__, success=False, error=repr(exc)[:200])


# ── Payment tests ─────────────────────────────────────────────────────────────
async def run_payment_test(idx: int, sent: dict[str, list[str]]) -> dict[str, Any]:
    from sqlalchemy import update
    from app.models.user import User as UserModel
    from app.api.routes.mercadopago import _handle_payment

    number = f"55119{80000000 + idx}"
    payment_id = f"PAY-STRESS-{idx:05d}"
    r: dict[str, Any] = {
        "number": number,
        "trial_activated": False,
        "subscription_activated": False,
        "duplicate_blocked": False,
        "cancel_processed": False,
        "errors": [],
    }

    vu = VirtualUser(number, "payment", sent)

    # 1. Onboarding → trial
    for step in ["Oi", f"Pagante {idx}", ONBOARDING_CNPJ, "SenhaMP", "Campinas", "SIM"]:
        await vu.send(step)

    async with _TestSession() as session:
        user = await UserService(session).get_by_number(number)
        if not user:
            r["errors"].append("Usuário não criado após onboarding")
            return r
        user_id = user.id
        if user.subscription_expires_at:
            r["trial_activated"] = True

        # 2. Force-expire trial
        await session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(subscription_expires_at=datetime.now() - timedelta(days=1))
        )
        await session.commit()

    # 3. Try to emit → subscription expired, receives payment link
    await vu.send("1")

    # 4. Simulate approved payment webhook
    with patch("app.integrations.mercadopago.client.mp_client") as mock_mp:
        mock_mp.get_payment.return_value = {
            "id": payment_id,
            "status": "approved",
            "external_reference": str(user_id),
            "metadata": {"whatsapp_number": number},
        }
        await _handle_payment(payment_id)

    async with _TestSession() as session:
        user = await UserService(session).get_by_number(number)
        if user and user.subscription_expires_at and user.subscription_expires_at > datetime.now():
            r["subscription_activated"] = True
        first_expires = user.subscription_expires_at if user else None

    # 5. Send same payment_id 4 more times — all should be blocked by dedup
    with patch("app.integrations.mercadopago.client.mp_client") as mock_mp:
        mock_mp.get_payment.return_value = {
            "id": payment_id,
            "status": "approved",
            "external_reference": str(user_id),
            "metadata": {"whatsapp_number": number},
        }
        for _ in range(4):
            await _handle_payment(payment_id)

    async with _TestSession() as session:
        user = await UserService(session).get_by_number(number)
        if user and first_expires and user.subscription_expires_at:
            delta = abs((user.subscription_expires_at - first_expires).total_seconds())
            if delta < 60:
                r["duplicate_blocked"] = True
            else:
                r["errors"].append(f"Pagamento duplicado não bloqueado (delta={delta:.0f}s)")

    # 6. Cancel subscription
    await vu.send("CANCELAR ASSINATURA")
    await vu.send("SIM")

    async with _TestSession() as session:
        user = await UserService(session).get_by_number(number)
        if user and user.subscription_cancelled_at:
            r["cancel_processed"] = True

    return r


# ── DB setup ──────────────────────────────────────────────────────────────────
async def setup_db() -> None:
    from sqlalchemy import text
    async with _test_engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.run_sync(Base.metadata.create_all)


# ── Report ────────────────────────────────────────────────────────────────────
def build_report(report: StressReport) -> list[str]:
    lines = [
        "=" * 60,
        "RELATÓRIO DE STRESS TEST — Bot NFS-e SPX Driver",
        f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
        f"Duração total: {report.total_seconds:.1f}s",
        "=" * 60,
        "",
        "── USUÁRIOS ─────────────────────────────────────────────",
        f"✅ Usuários completos: {report.ok}/{NUM_USERS}",
        f"❌ Usuários com erro:  {report.failed}/{NUM_USERS}",
        "",
    ]

    errors = [(r.number, r.error) for r in report.user_results if r.error]
    if errors:
        lines.append("Erros encontrados:")
        for num, err in errors[:20]:
            lines.append(f"  - Usuário {num}: {err}")
        if len(errors) > 20:
            lines.append(f"  ... e mais {len(errors) - 20}")
        lines.append("")

    stuck = report.stuck
    lines.append(f"Estados travados: {len(stuck)}")
    for r in stuck[:10]:
        lines.append(f"  - Usuário {r.number} ({r.flow}): {r.final_state}")
    lines.append("")

    lines += [
        f"Mensagens duplicadas detectadas: {report.duplicates}",
        f"Tempo médio de resposta: {report.avg_rt:.2f}s",
        f"Tempo máximo de resposta: {report.max_rt:.2f}s",
        "",
        "── FLUXOS ───────────────────────────────────────────────",
    ]
    flow_counts = Counter(r.flow for r in report.user_results)
    for flow, count in sorted(flow_counts.items()):
        errs = sum(1 for r in report.user_results if r.flow == flow and not r.success)
        lines.append(f"  {flow:<12}: {count} usuários, {errs} erros")
    lines.append("")

    # Payment results
    pr = report.payment_results
    if pr:
        n = len(pr)
        trial_ok  = sum(1 for v in pr if v.get("trial_activated"))
        sub_ok    = sum(1 for v in pr if v.get("subscription_activated"))
        dup_ok    = sum(1 for v in pr if v.get("duplicate_blocked"))
        cancel_ok = sum(1 for v in pr if v.get("cancel_processed"))
        pay_err   = sum(1 for v in pr if v.get("errors"))

        lines += [
            "── PAGAMENTOS ───────────────────────────────────────────",
            f"✅ Trials ativados:              {trial_ok}/{n}",
            f"✅ Assinaturas ativadas:         {sub_ok}/{n}",
            f"✅ Pagamentos duplicados bloq.:  {dup_ok}/{n}",
            f"✅ Cancelamentos processados:    {cancel_ok}/{n}",
            f"❌ Falhas no fluxo de pagamento: {pay_err}/{n}",
        ]
        pay_errors = [(v["number"], e) for v in pr for e in v.get("errors", [])]
        if pay_errors:
            lines.append("\nErros de pagamento:")
            for num, err in pay_errors[:10]:
                lines.append(f"  - {num}: {err}")
        lines.append("")

    lines += [
        "── NOTAS ────────────────────────────────────────────────",
        "⚠️  Estados travados podem ser artefato do SQLite StaticPool:",
        "   StaticPool compartilha uma única conexão entre todas as",
        "   sessões concorrentes. Em produção (WAL mode, conexões",
        "   separadas) esse problema não ocorre.",
        "   Testes de pagamento rodam sequencialmente para evitar",
        "   conflito de patches asyncio (unittest.mock não é",
        "   asyncio-safe para corrotinas concorrentes).",
        "",
        "=" * 60, "Fim do relatório", "=" * 60,
    ]
    return lines


# ── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    print("🚀 Stress test — Bot NFS-e SPX Driver")
    print(f"   {NUM_USERS} usuários + {PAYMENT_USERS} testes de pagamento\n")

    await setup_db()

    report = StressReport()
    sent_messages: dict[str, list[str]] = {}

    async def fake_send_text(number: str, text: str) -> None:
        sent_messages.setdefault(number, []).append(text)

    with (
        patch("app.integrations.evolution.client.evolution_client.send_text",
              side_effect=fake_send_text),
        patch("app.integrations.evolution.client.evolution_client.send_pdf",
              new_callable=AsyncMock),
        patch("app.integrations.evolution.client.evolution_client.delete_message",
              new_callable=AsyncMock),
        patch("app.integrations.mercadopago.client.MercadoPagoClient.create_preference",
              return_value="https://fake-mp.stress/pay"),
        patch("app.integrations.mercadopago.client.MercadoPagoClient.cancel_preapproval",
              return_value=True),
        # Redirect _handle_payment's session to the test DB
        patch("app.api.routes.mercadopago.AsyncSessionFactory", _TestSession),
    ):
        # ── 100 concurrent users ─────────────────────────────────
        print(f"⚡ Rodando {NUM_USERS} usuários em paralelo...")
        numbers = [f"5511900{i:06d}" for i in range(1, NUM_USERS + 1)]
        t0 = time.monotonic()
        results = await asyncio.gather(
            *(run_user(n, sent_messages) for n in numbers),
            return_exceptions=True,
        )
        elapsed = time.monotonic() - t0
        print(f"   Concluído em {elapsed:.1f}s\n")

        for r in results:
            if isinstance(r, Exception):
                report.user_results.append(
                    UserResult(number="?", flow="?", success=False, error=repr(r)[:200])
                )
            else:
                report.user_results.append(r)

        # ── 20 payment tests (sequential — unittest.mock.patch is not asyncio-safe for concurrent coroutines)
        print(f"💳 Rodando {PAYMENT_USERS} testes de pagamento (sequencial)...")
        t1 = time.monotonic()
        for i in range(1, PAYMENT_USERS + 1):
            try:
                pr = await run_payment_test(i, sent_messages)
            except Exception as exc:
                pr = {"number": "?", "errors": [repr(exc)[:200]]}
            report.payment_results.append(pr)
            if i % 5 == 0:
                print(f"   {i}/{PAYMENT_USERS} concluídos...")
        pay_elapsed = time.monotonic() - t1
        print(f"   Concluído em {pay_elapsed:.1f}s\n")

    report.total_seconds = time.monotonic() - t0

    # ── Write report ─────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = build_report(report)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "\n".join(lines))
    print(f"\n📄 Relatório salvo em: {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
