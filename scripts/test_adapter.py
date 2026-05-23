"""
scripts/test_adapter.py
Testa o NacionalAdapter localmente com browser visível (headless=False).
Uso: python scripts/test_adapter.py
"""
import os
import sys
from pathlib import Path

# Configura env antes de qualquer import do app
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/nfse.db")
os.environ.setdefault("DRY_RUN", "false")
os.environ.setdefault("NFSE_DRY_RUN", "false")
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("ENCRYPTION_KEY", "s2NRx9dFAeC66-RpwiE2TNEN0wGhr9UwMlPGuO6oZ24=")
os.environ.setdefault("MERCADOPAGO_ACCESS_TOKEN", "test")
os.environ.setdefault("MERCADOPAGO_PLAN_ID", "test")
os.environ.setdefault("EVOLUTION_URL", "http://localhost")
os.environ.setdefault("EVOLUTION_API_KEY", "test")
os.environ.setdefault("EVOLUTION_INSTANCE", "test")
os.environ.setdefault("OUTPUT_DIR", "./output")
os.environ.setdefault("LOGS_DIR", "./logs")
os.environ.setdefault("DATA_DIR", "./data")

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.adapters.nacional_adapter import NacionalAdapter
from app.adapters.base_adapter import EmissionRequest
from app.core.config import settings

settings.ensure_directories()

# ── Preencha com suas credenciais reais ──────────────────────────────────────
CNPJ     = "56.805.503/0001-99"
SENHA    = input("Digite a senha do portal: ")
PERIODO  = "11/05 a 17/05/2026"
VALOR    = 100.47
MUNICIPIO = "Campinas/SP"
# ─────────────────────────────────────────────────────────────────────────────

request = EmissionRequest(
    value=VALOR,
    period=PERIODO,
    username=CNPJ,
    password=SENHA,
    portal_url="https://www.nfse.gov.br/EmissorNacional/Login",
    prestador_nome="Teste",
    prestador_cnpj=CNPJ,
    tomador_cnpj="42.446.277/0001-92",
    tomador_razao_social="SHOPEE INTERNACIONAL BRASIL LTDA",
    service_description="Serviços de entrega",
    municipio=MUNICIPIO,
    headless=True,    # simula o Railway
    slow_mo=0,
    user_id=0,
)

class NacionalAdapterDryTest(NacionalAdapter):
    """Para antes de emitir — só testa o preenchimento do formulário."""
    def _emitir(self, page):
        input("\n✋ Formulário preenchido. Verifique o browser e pressione ENTER para fechar.")

    def _extrair_numero_nota(self, page):
        return "TEST-OK"

    def _baixar_pdf(self, page, request):
        return None

adapter = NacionalAdapterDryTest()

print("\n🚀 Iniciando teste de formulário (SEM emitir nota)...\n")
try:
    result = adapter.emit(request)
    print(f"\n✅ Formulário preenchido com sucesso — nenhuma nota emitida.")
except Exception as e:
    print(f"\n❌ Erro: {e}")
