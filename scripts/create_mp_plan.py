"""
scripts/create_mp_plan.py
Cria o plano de assinatura recorrente no Mercado Pago e salva o plan_id no .env.

Uso:
    python scripts/create_mp_plan.py
"""
import json
import re
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

ENV_PATH = Path(__file__).parent.parent / ".env"


def load_token() -> str:
    env = dotenv_values(ENV_PATH)
    token = env.get("MERCADOPAGO_ACCESS_TOKEN", "").strip()
    if not token:
        print("❌ MERCADOPAGO_ACCESS_TOKEN não encontrado no .env")
        sys.exit(1)
    return token


def load_back_url() -> str:
    env = dotenv_values(ENV_PATH)
    return env.get("BOT_PUBLIC_URL", "https://www.mercadopago.com.br").strip()


def load_price() -> float:
    env = dotenv_values(ENV_PATH)
    try:
        return float(env.get("SUBSCRIPTION_PRICE", "19.90"))
    except ValueError:
        return 19.90


def create_plan(token: str, back_url: str) -> dict:
    price = load_price()
    payload = {
        "reason": "Assinatura Bot NFS-e SPX Driver",
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": price,
            "currency_id": "BRL",
        },
        "back_url": back_url,
    }

    response = requests.post(
        "https://api.mercadopago.com/preapproval_plan",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if response.status_code not in (200, 201):
        print(f"❌ Erro ao criar plano (HTTP {response.status_code}):")
        try:
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        except Exception:
            print(response.text)
        sys.exit(1)

    return response.json()


def save_plan_id(plan_id: str) -> None:
    content = ENV_PATH.read_text(encoding="utf-8")

    if "MERCADOPAGO_PLAN_ID" in content:
        content = re.sub(
            r"^MERCADOPAGO_PLAN_ID=.*$",
            f"MERCADOPAGO_PLAN_ID={plan_id}",
            content,
            flags=re.MULTILINE,
        )
    else:
        # Insere logo após MERCADOPAGO_WEBHOOK_SECRET ou MERCADOPAGO_ACCESS_TOKEN
        match = re.search(r"^(MERCADOPAGO_WEBHOOK_SECRET|MERCADOPAGO_ACCESS_TOKEN)=.*$", content, re.MULTILINE)
        if match:
            insert_pos = match.end()
            content = content[:insert_pos] + f"\nMERCADOPAGO_PLAN_ID={plan_id}" + content[insert_pos:]
        else:
            content += f"\nMERCADOPAGO_PLAN_ID={plan_id}\n"

    ENV_PATH.write_text(content, encoding="utf-8")


def main() -> None:
    token = load_token()
    back_url = load_back_url()

    price = load_price()
    print(f"🔄 Criando plano no Mercado Pago...")
    print(f"   Preço:    R$ {price:.2f}/mês")
    print(f"   back_url: {back_url}")

    data = create_plan(token, back_url)
    plan_id = data.get("id", "")

    if not plan_id:
        print("❌ Resposta inesperada do MP (sem campo 'id'):")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        sys.exit(1)

    save_plan_id(plan_id)

    print(f"\n✅ Plano criado com sucesso!")
    print(f"   Plan ID: {plan_id}")
    print(f"   Status:  {data.get('status', '?')}")
    print(f"   Salvo no .env como MERCADOPAGO_PLAN_ID")


if __name__ == "__main__":
    main()
