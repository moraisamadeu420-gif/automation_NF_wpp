"""
config/settings.py
Configurações centralizadas carregadas do .env
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega o .env da raiz do projeto
load_dotenv(Path(__file__).parent.parent / ".env")


class NfseConfig:
    """Configurações do portal NFS-e"""
    URL: str = os.getenv("NFSE_URL", "")
    USUARIO: str = os.getenv("NFSE_USUARIO", "")
    SENHA: str = os.getenv("NFSE_SENHA", "")
    MUNICIPIO: str = os.getenv("NFSE_MUNICIPIO", "")


class PrestadorConfig:
    """Dados do prestador de serviços (você)"""
    NOME: str = os.getenv("PRESTADOR_NOME", "")
    CNPJ: str = os.getenv("PRESTADOR_CNPJ", "")
    CPF: str = os.getenv("PRESTADOR_CPF", "")
    IE: str = os.getenv("PRESTADOR_IE", "ISENTO")


class TomadorConfig:
    """Dados do tomador (Shopee/SPX)"""
    CNPJ: str = os.getenv("TOMADOR_CNPJ", "15.057.629/0001-48")
    RAZAO_SOCIAL: str = os.getenv("TOMADOR_RAZAO_SOCIAL", "SHOPEE COMERCIO DIGITAL DO BRASIL LTDA")
    ENDERECO: str = os.getenv("TOMADOR_ENDERECO", "")


class ServicoConfig:
    """Dados do serviço prestado"""
    DESCRICAO_TEMPLATE: str = os.getenv(
        "SERVICO_DESCRICAO",
        "Serviços de entrega e logística prestados como motorista parceiro SPX Driver - Período: {PERIODO}"
    )
    CODIGO_MUNICIPIO: str = os.getenv("SERVICO_CODIGO_MUNICIPIO", "1.01")
    ALIQUOTA_ISS: float = float(os.getenv("SERVICO_ALIQUOTA_ISS", "2.0"))
    CODIGO_CNAE: str = os.getenv("SERVICO_CODIGO_CNAE", "5320100")
    NOME_FAVORITO: str = os.getenv("SERVICO_NOME_FAVORITO", "Amadeu")

class AndroidConfig:
    """Configurações Android/Appium"""
    DEVICE_SERIAL: str = os.getenv("ANDROID_DEVICE_SERIAL", "")
    APPIUM_URL: str = os.getenv("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
    SPX_PACKAGE: str = os.getenv("SPX_PACKAGE", "com.shopee.spxdriver")
    SPX_ACTIVITY: str = os.getenv("SPX_ACTIVITY", ".MainActivity")

    # Capabilities para o Appium
    CAPABILITIES: dict = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:appPackage": SPX_PACKAGE,
        "appium:appActivity": SPX_ACTIVITY,
        "appium:noReset": True,           # Não limpa dados do app
        "appium:dontStopAppOnReset": True,
        "appium:newCommandTimeout": 120,
    }


class AutomacaoConfig:
    """Configurações de comportamento da automação"""
    SEMI_AUTO: bool = os.getenv("SEMI_AUTO", "true").lower() == "true"
    VELOCIDADE: str = os.getenv("VELOCIDADE", "normal")
    HEADLESS: bool = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"

    # Delays em segundos por velocidade (min, max)
    DELAYS = {
        "slow":   {"curto": (2, 4),   "medio": (4, 7),    "longo": (7, 12)},
        "normal": {"curto": (0.8, 2), "medio": (2, 4),    "longo": (4, 8)},
        "fast":   {"curto": (0.3, 0.8), "medio": (0.8, 2), "longo": (2, 4)},
    }


class PathConfig:
    """Caminhos de arquivos e pastas"""
    BASE: Path = Path(__file__).parent.parent
    OUTPUT: Path = Path(os.getenv("OUTPUT_DIR", "./output"))
    LOGS: Path = Path(os.getenv("LOGS_DIR", "./logs"))
    DATA: Path = Path(os.getenv("DATA_DIR", "./data"))
    NOTAS: Path = OUTPUT / "notas"
    PDF: Path = OUTPUT / "pdf"
    XML: Path = OUTPUT / "xml"
    HISTORICO_CSV: Path = DATA / "historico.csv"
    HISTORICO_XLSX: Path = DATA / "historico_notas.xlsx"

    @classmethod
    def garantir_pastas(cls):
        """Cria todas as pastas necessárias se não existirem"""
        for path in [cls.OUTPUT, cls.LOGS, cls.DATA, cls.NOTAS, cls.PDF, cls.XML]:
            path.mkdir(parents=True, exist_ok=True)
