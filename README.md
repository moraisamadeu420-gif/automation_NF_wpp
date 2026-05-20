# NFSe Bot

Automacao de emissao de Nota Fiscal de Servico Eletroncia (NFS-e) via WhatsApp.

O usuario envia um print de ganhos no SPX Driver. O bot le o valor via OCR, acessa o portal da prefeitura via Playwright e devolve o PDF da nota no proprio WhatsApp.

---

## Arquitetura

```
WhatsApp (Evolution API)
        |
        v webhook POST /webhook
+------------------------+
|   FastAPI (main.py)    |
|   api/routes/webhook   |
+----------+-------------+
           |
           v
+------------------------------+
|      MessageProcessor        |  State machine por usuario
|  services/message_processor  |
+------+-----------+-----------+
       |           |
       v           v
+----------+  +-----------+
|OcrService|  |NfseService|
|  (Groq)  |  |(Playwright)|
+----------+  +-----+-----+
                    |
              +-----v------+
              |  Adapters  |
              | nacional / |
              | campinas   |
              +------------+
```

### Camadas

| Camada | Responsabilidade |
|---|---|
| `app/core` | Configuracao, logging, excecoes, seguranca |
| `app/models` | Entidades SQLAlchemy |
| `app/database` | Engine async, factory de sessao |
| `app/repositories` | CRUD isolado por entidade |
| `app/adapters` | Automacao Playwright por prefeitura |
| `app/integrations` | Clientes externos (Evolution, Groq) |
| `app/services` | Logica de negocio |
| `app/schemas` | DTOs Pydantic (entrada/saida da API) |
| `app/api` | Rotas FastAPI, middlewares, dependencias |
| `app/workers` | Preparado para fila de tarefas futura |

---

## Setup

### 1. Pre-requisitos

- Python 3.11+
- Evolution API rodando e acessivel (Railway, VPS, etc.)
- Conta no Groq Cloud (groq.com)

### 2. Instalacao

```bash
# Cria venv, instala dependencias e baixa o Chromium
make install
```

### 3. Configuracao

```bash
cp .env.example .env
# Edite .env com suas chaves
```

Variaveis obrigatorias:

```
EVOLUTION_URL=https://sua-evolution-api.com
EVOLUTION_API_KEY=sua_chave
GROQ_API_KEY=sua_chave_groq
```

### 4. Iniciar

```bash
# Desenvolvimento (reload automatico)
make dev

# Producao
make start
```

### 5. Expor o webhook (desenvolvimento local)

Use ngrok para receber webhooks da Evolution API:

```bash
ngrok http 8000
```

Configure na Evolution API:
- URL: `https://seu-ngrok.ngrok.io/webhook`
- Evento: `MESSAGES_UPSERT`

---

## Fluxo de uso

1. Usuario manda qualquer mensagem no WhatsApp
2. Bot detecta usuario novo e inicia onboarding (coleta credenciais NFSe)
3. Usuario configurado ve o menu de opcoes
4. Usuario escolhe emitir nota e envia print do SPX Driver
5. Bot le o valor via OCR (Groq Vision)
6. Bot confirma valor com o usuario
7. Bot acessa o portal via Playwright e emite a nota
8. PDF e enviado de volta no WhatsApp

---

## Adicionar nova prefeitura

1. Crie `app/adapters/novacidade_adapter.py` implementando `BaseNfseAdapter`
2. Registre no `app/adapters/__init__.py`

```python
from app.adapters.novacidade_adapter import NovaCidadeAdapter

_REGISTRY = {
    ...
    NovaCidadeAdapter().municipality_key: NovaCidadeAdapter(),
}
```

---

## Endpoints

| Metodo | Rota | Descricao |
|---|---|---|
| GET | `/health` | Status da API e conexao WhatsApp |
| POST | `/webhook` | Recebe eventos da Evolution API |
| GET | `/docs` | Swagger (apenas com DEBUG=true) |

---

## Migracao para PostgreSQL

Altere apenas `DATABASE_URL` no `.env`:

```
DATABASE_URL=postgresql+asyncpg://user:senha@host:5432/nfse
```

```bash
pip install asyncpg
make migrate
```

Nenhum outro codigo precisa ser alterado.

---

## Roadmap

- [ ] Criptografia de senhas armazenadas (Fernet)
- [ ] Adapter Campinas (campinas.nfse.com.br)
- [ ] Adapter Sao Paulo (nfe.prefeitura.sp.gov.br)
- [ ] Fila de tarefas com ARQ
- [ ] Multi-tenant com planos e limites de emissao
- [ ] CI/CD com GitHub Actions
