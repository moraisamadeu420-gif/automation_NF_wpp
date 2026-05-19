import re, json, os, base64
from pathlib import Path
from datetime import datetime
from loguru import logger
from groq import Groq


def ler_ganhos_do_print(caminho_imagem: Path) -> dict | None:
    logger.info(f"🔍 Analisando imagem: {caminho_imagem.name}")
    if not caminho_imagem.exists():
        logger.error(f"❌ Imagem não encontrada: {caminho_imagem}")
        return None
    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        with open(caminho_imagem, "rb") as f:
            imagem_b64 = base64.b64encode(f.read()).decode("utf-8")
        sufixo = caminho_imagem.suffix.lower()
        mime = "image/jpeg" if sufixo in [".jpg", ".jpeg"] else "image/png"
        resposta = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{imagem_b64}"}},
                    {"type": "text", "text": "Analise o print do SPX Driver da Shopee. Encontre o valor total de ganhos da semana e o periodo. Responda SOMENTE com JSON puro, sem texto antes ou depois: {\"valor\": 350.00, \"periodo\": \"12/05 a 18/05/2026\"}"}
                ]
            }],
            max_tokens=150,
        )
        texto = resposta.choices[0].message.content.strip()
        logger.debug(f"🤖 Resposta: {texto}")

        # Tenta extrair JSON mesmo que venha com texto ao redor
        texto = re.sub(r"```json|```", "", texto).strip()
        match = re.search(r'\{.*?\}', texto, re.DOTALL)
        if match:
            texto = match.group(0)

        dados = json.loads(texto)
        valor = dados.get("valor")
        periodo = dados.get("periodo") or datetime.now().strftime("semana de %d/%m/%Y")
        if valor is None:
            logger.error("❌ Não foi possível extrair o valor")
            return None
        valor = float(valor)
        logger.success(f"✅ Valor lido: R$ {valor:.2f} | Período: {periodo}")
        return {"valor": valor, "periodo": periodo, "data_leitura": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"❌ Erro no OCR: {e}")
        return None


def solicitar_upload_imagem() -> Path | None:
    print("\n" + "=" * 60)
    print("📱 UPLOAD DO PRINT DO SPX DRIVER")
    print("=" * 60)
    print("1. Abra o app SPX Driver no celular")
    print("2. Vá até a tela de Ganhos da semana")
    print("3. Tire um print e mande por AirDrop para o Mac")
    print("-" * 60)
    while True:
        caminho_str = input("📂 Cole o caminho da imagem (ou Enter para cancelar): ").strip()
        if not caminho_str:
            return None
        caminho_str = caminho_str.strip("'\"")
        caminho = Path(caminho_str)
        if caminho.exists() and caminho.suffix.lower() in [".png", ".jpg", ".jpeg"]:
            return caminho
        print("❌ Arquivo não encontrado ou formato inválido. Use PNG ou JPG.")