"""
modules/whatsapp.py
Envia o PDF da nota fiscal via WhatsApp usando Evolution API.
"""
import os
import base64
from pathlib import Path
from loguru import logger
import requests


EVOLUTION_URL = os.getenv("EVOLUTION_URL", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
WHATSAPP_DESTINO = os.getenv("WHATSAPP_DESTINO", "")
INSTANCE_NAME = "spx-nfse"


def criar_instancia() -> bool:
    """Cria a instância do WhatsApp na Evolution API."""
    try:
        response = requests.post(
            f"{EVOLUTION_URL}/instance/create",
            headers={"apikey": EVOLUTION_API_KEY},
            json={
                "instanceName": INSTANCE_NAME,
                "qrcode": True,
                "integration": "WHATSAPP-BAILEYS"
            }
        )
        logger.debug(f"Instância criada: {response.status_code}")
        return response.status_code in [200, 201]
    except Exception as e:
        logger.error(f"❌ Erro ao criar instância: {e}")
        return False


def obter_qrcode() -> str | None:
    """Obtém o QR Code para conectar o WhatsApp."""
    try:
        response = requests.get(
            f"{EVOLUTION_URL}/instance/connect/{INSTANCE_NAME}",
            headers={"apikey": EVOLUTION_API_KEY}
        )
        dados = response.json()
        return dados.get("base64") or dados.get("qrcode", {}).get("base64")
    except Exception as e:
        logger.error(f"❌ Erro ao obter QR Code: {e}")
        return None


def verificar_conexao() -> bool:
    """Verifica se o WhatsApp está conectado."""
    try:
        response = requests.get(
            f"{EVOLUTION_URL}/instance/connectionState/{INSTANCE_NAME}",
            headers={"apikey": EVOLUTION_API_KEY}
        )
        dados = response.json()
        estado = dados.get("instance", {}).get("state", "")
        return estado == "open"
    except Exception as e:
        logger.error(f"❌ Erro ao verificar conexão: {e}")
        return False


def conectar_whatsapp() -> bool:
    """Conecta o WhatsApp via QR Code."""
    logger.info("📱 Conectando WhatsApp...")

    # Cria instância se não existir
    criar_instancia()

    import time
    time.sleep(2)

    if verificar_conexao():
        logger.success("✅ WhatsApp já conectado!")
        return True

    # Obtém QR Code
    qr = obter_qrcode()
    if not qr:
        logger.error("❌ Não foi possível obter o QR Code")
        return False

    # Salva QR Code como imagem
    qr_path = Path("output/qrcode.png")
    qr_path.parent.mkdir(exist_ok=True)

    if qr.startswith("data:image"):
        qr = qr.split(",")[1]

    with open(qr_path, "wb") as f:
        f.write(base64.b64decode(qr))

    print("\n" + "=" * 60)
    print("📱 CONECTE SEU WHATSAPP")
    print("=" * 60)
    print(f"1. Abra o arquivo: {qr_path.absolute()}")
    print("2. Abra o WhatsApp no celular")
    print("3. Vá em Configurações → Dispositivos conectados")
    print("4. Escaneie o QR Code")
    print("-" * 60)
    input("▶️  Pressione Enter após escanear o QR Code...")

    if verificar_conexao():
        logger.success("✅ WhatsApp conectado!")
        return True

    logger.error("❌ WhatsApp não conectado")
    return False


def enviar_pdf(caminho_pdf: Path, numero_nota: str = None, valor: float = None) -> bool:
    """
    Envia o PDF da nota fiscal via WhatsApp.

    Args:
        caminho_pdf: Path do arquivo PDF
        numero_nota: Número da nota para a mensagem
        valor: Valor da nota para a mensagem
    """
    if not caminho_pdf or not caminho_pdf.exists():
        logger.error("❌ PDF não encontrado para envio")
        return False

    logger.info(f"📤 Enviando PDF via WhatsApp: {caminho_pdf.name}")

    try:
        # Converte PDF para base64
        with open(caminho_pdf, "rb") as f:
            pdf_b64 = base64.b64encode(f.read()).decode("utf-8")

        # Monta mensagem
        mensagem = "✅ *NFS-e emitida com sucesso!*\n"
        if numero_nota:
            mensagem += f"📋 Nota Nº: {numero_nota}\n"
        if valor:
            mensagem += f"💰 Valor: R$ {valor:.2f}\n"
        mensagem += "\nSegue o PDF em anexo."

        # Envia mensagem de texto
        requests.post(
            f"{EVOLUTION_URL}/message/sendText/{INSTANCE_NAME}",
            headers={"apikey": EVOLUTION_API_KEY},
            json={
                "number": WHATSAPP_DESTINO,
                "text": mensagem
            }
        )

        import time
        time.sleep(1)

        # Envia PDF
        response = requests.post(
            f"{EVOLUTION_URL}/message/sendMedia/{INSTANCE_NAME}",
            headers={"apikey": EVOLUTION_API_KEY},
            json={
                "number": WHATSAPP_DESTINO,
                "mediatype": "document",
                "mimetype": "application/pdf",
                "caption": f"NFS-e {numero_nota or ''}",
                "media": pdf_b64,
                "fileName": caminho_pdf.name
            }
        )

        if response.status_code in [200, 201]:
            logger.success(f"✅ PDF enviado via WhatsApp!")
            return True
        else:
            logger.error(f"❌ Erro ao enviar: {response.text}")
            return False

    except Exception as e:
        logger.error(f"❌ Erro ao enviar PDF: {e}")
        return False