"""
scripts/simulate_image.py
Simula um webhook MESSAGES_UPSERT com imagem da Evolution API.
Uso: make webhook-test-image IMG=/path/to/print.jpg NUMBER=5541999000001
"""
import argparse
import base64
import json
import sys
import time
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--number", default="5541999000001")
    parser.add_argument("--image", required=True)
    parser.add_argument("--host", default="http://localhost:8000")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        print(f"Erro: imagem nao encontrada: {image_path}")
        sys.exit(1)

    suffix = image_path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    payload = {
        "event": "MESSAGES_UPSERT",
        "instance": "spx-nfse",
        "data": {
            "key": {
                "remoteJid": f"{args.number}@s.whatsapp.net",
                "fromMe": False,
                "id": f"TEST-IMG-{int(time.time())}",
            },
            "message": {
                "imageMessage": {
                    "mimetype": mime,
                    "jpegThumbnail": b64[:100],
                }
            },
            "messageType": "imageMessage",
            "pushName": "Driver Teste",
        },
    }

    print(f"Enviando imagem '{image_path.name}' ({len(b64)} bytes b64) como {args.number}...")

    # O endpoint /webhook nao processa a imagem inline — ele chama download_media()
    # que busca na Evolution API. Para teste local completo, usamos o OCR direto.
    # Por isso enviamos via /webhook/test-ocr se disponivel, senao mostra instrucao.
    try:
        r = httpx.post(f"{args.host}/webhook", json=payload, timeout=15)
        print(f"Status: {r.status_code}")
        print(json.dumps(r.json(), indent=2))
    except httpx.ConnectError:
        print(f"Erro: servidor nao encontrado em {args.host}. Execute 'make dev' primeiro.")
        sys.exit(1)


if __name__ == "__main__":
    main()
