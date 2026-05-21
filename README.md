# Bot NFSe — Emissão automática de nota fiscal via WhatsApp

Bot que automatiza a emissão de NFS-e (Nota Fiscal de Serviços Eletrônica) pelo WhatsApp para motoristas parceiros da SPX/Shopee. O usuário informa o valor da semana e o bot acessa o portal [nfse.gov.br](https://www.nfse.gov.br) automaticamente, preenche todos os campos e devolve o PDF da nota no próprio WhatsApp.

---

## Como funciona

```
Usuário WhatsApp
      │
      ▼ webhook
Evolution API ──► FastAPI (main.py)
                        │
                        ▼
               MessageProcessor          ← máquina de estados por usuário
                   │         │
                   ▼         ▼
            NfseService   Histórico
                   │
                   ▼
          NacionalAdapter (Playwright)
                   │
                   ▼
          Portal nfse.gov.br
```

**Fluxo do usuário:**

1. Manda qualquer mensagem → bot inicia o cadastro (CNPJ, senha, município)
2. Após configurado, escolhe `1` ou digita `INICIAR`
3. Bot pergunta o valor da semana
4. Usuário confirma → bot abre o navegador, preenche o formulário e emite a nota
5. PDF da nota é enviado de volta no WhatsApp

---

## Pré-requisitos

| Ferramenta | Para quê | Link |
|---|---|---|
| **Python 3.12+** | Rodar o projeto | [python.org](https://www.python.org/downloads/) |
| **Git** | Clonar o repositório | [git-scm.com](https://git-scm.com/) |
| **ngrok** | Expor o servidor local para receber webhooks | [ngrok.com](https://ngrok.com/) |
| **Railway** | Hospedar a Evolution API gratuitamente | [railway.app](https://railway.app/) |
| **DBeaver** *(opcional)* | Visualizar o banco SQLite | [dbeaver.io](https://dbeaver.io/) |

---

## Instalação passo a passo

### 1. Clonar o repositório

```bash
git clone https://github.com/seu-usuario/bot-nfse.git
cd bot-nfse
```

### 2. Criar e ativar o ambiente virtual

```bash
python3 -m venv .venv

# macOS / Linux:
source .venv/bin/activate

# Windows:
.venv\Scripts\activate
```

### 3. Instalar dependências e o Chromium

```bash
# Instala tudo de uma vez:
make install

# Ou manualmente:
pip install -r requirements.txt
playwright install chromium
```

### 4. Configurar o `.env`

```bash
cp .env.example .env
```

Abra o `.env` e preencha as variáveis (veja a seção abaixo).

---

## Configuração do `.env`

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DATABASE_URL` | Sim | URL do banco. Padrão SQLite funciona sem instalação. |
| `EVOLUTION_URL` | Sim | URL da sua Evolution API no Railway. |
| `EVOLUTION_API_KEY` | Sim | Chave de API da Evolution (criada no deploy). |
| `EVOLUTION_INSTANCE` | Sim | Nome da instância. Use `spx-nfse`. |
| `ENCRYPTION_KEY` | Gerado auto | Chave Fernet para criptografar senhas. Deixe em branco na primeira execução — o bot gera e salva automaticamente. **Depois copie o valor gerado para o `.env` e guarde em local seguro.** |
| `DRY_RUN` | Não | `true` = simula a emissão sem abrir o Playwright. Útil para testar o fluxo de conversação. |
| `BROWSER_HEADLESS` | Não | `false` = abre o Chrome visível (bom para depurar). `true` = roda em segundo plano (produção). |
| `BROWSER_SLOW_MO` | Não | Atraso em ms entre ações do Playwright. `800` é seguro para o portal. |
| `WEBHOOK_SECRET` | Não | Se preenchido, valida assinatura HMAC dos webhooks da Evolution. |

---

## Configurar a Evolution API no Railway

### 1. Deploy do template

1. Acesse [railway.app](https://railway.app/) e crie uma conta
2. Clique em **New Project → Deploy a Template**
3. Busque por **Evolution API** e faça o deploy
4. Após o deploy, anote a URL pública gerada (ex: `https://evolution-api-xyz.up.railway.app`)

### 2. Criar a instância

Acesse `https://sua-evolution-url.up.railway.app/manager` e:

1. Clique em **Create Instance**
2. Nome da instância: `spx-nfse`
3. Copie a **API Key** gerada e coloque no `.env` como `EVOLUTION_API_KEY`

### 3. Conectar o WhatsApp via QR Code

No manager, abra a instância `spx-nfse` e clique em **Connect**.
Escaneie o QR Code com o WhatsApp do número que vai usar como bot.

---

## Configurar o webhook

O webhook conecta a Evolution API ao servidor local via ngrok.

### 1. Iniciar o ngrok

```bash
ngrok http 8000
```

Anote a URL gerada (ex: `https://abc123.ngrok-free.app`).

### 2. Registrar o webhook na Evolution API

```bash
curl -X POST https://sua-evolution-url.up.railway.app/webhook/set/spx-nfse \
  -H "apikey: SUA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
      "enabled": true,
      "url": "https://abc123.ngrok-free.app/webhook",
      "events": ["MESSAGES_UPSERT"]
    }
  }'
```

---

## Rodar o projeto

```bash
# Desenvolvimento (reinicia automaticamente ao salvar)
make dev

# Produção
make start
```

O servidor inicia em `http://localhost:8000`.
Com `DEBUG=true`, o Swagger fica disponível em `http://localhost:8000/docs`.

---

## Testar o fluxo completo

### Teste rápido via terminal (sem WhatsApp)

```bash
# Envia "oi" como se fosse uma mensagem WhatsApp
make webhook-test TEXT="oi"

# Envia outros textos
make webhook-test TEXT="1"
make webhook-test TEXT="INICIAR"
```

### Teste completo pelo WhatsApp

Com `DRY_RUN=true` no `.env`, o bot simula a emissão sem abrir o Playwright:

1. Mande qualquer mensagem para o número do bot
2. Faça o cadastro: informe nome, CNPJ, senha e município
3. Confirme os dados com `SIM`
4. No menu, escolha `1` ou `INICIAR`
5. Informe o valor (ex: `697,08`)
6. Confirme com `SIM`
7. Bot responde: `NFS-e emitida com sucesso! Nota N: DRY-RUN-001`

Quando tudo estiver funcionando, mude para `DRY_RUN=false` para emissões reais.

---

## Comandos úteis

| Comando | Descrição |
|---|---|
| `make install` | Cria o venv, instala dependências e baixa o Chromium |
| `make dev` | Inicia o servidor com reload automático |
| `make start` | Inicia o servidor em modo produção |
| `make webhook-test TEXT="oi"` | Simula uma mensagem WhatsApp via curl |
| `make scheduler-test` | Dispara manualmente o job semanal de segunda-feira |
| `make health` | Checa se o servidor e o WhatsApp estão conectados |
| `make migrate` | Roda as migrations Alembic |
| `make lint` | Roda ruff e mypy |
| `make clean` | Remove arquivos `__pycache__` e `.pyc` |

---

## Solução de problemas comuns

**`database is locked`**
O SQLite travou (raro em desenvolvimento). Reinicie o servidor:
```bash
# Ctrl+C para parar, depois:
make dev
```

**`Connection Closed` ou `QR Code expirado` na Evolution API**
O WhatsApp desconectou. Acesse o manager da Evolution API, abra a instância `spx-nfse` e clique em **Reconnect**. Escaneie o QR Code novamente.

**Timeout no Playwright (`TimeoutError`)**
O portal nfse.gov.br estava lento. Tente novamente. Se persistir, aumente `BROWSER_SLOW_MO` para `1200` no `.env`.

**Bot não recebe mensagens (webhook não chega)**
Verifique se o ngrok ainda está rodando e se a URL foi registrada corretamente na Evolution API. Repita o comando `curl` de registro do webhook com a URL atual do ngrok.

**Limpar o banco e começar do zero**
```bash
rm data/nfse.db
make dev   # o banco é recriado automaticamente no startup
```

---

## Estrutura do projeto

```
bot-nfse/
├── main.py                        # Ponto de entrada FastAPI + lifespan
├── Makefile                       # Comandos de desenvolvimento
├── requirements.txt               # Dependências Python
├── .env.example                   # Modelo do arquivo de configuração
│
├── app/
│   ├── adapters/                  # Automação Playwright por portal
│   │   ├── base_adapter.py        # Contrato base (ABC)
│   │   └── nacional_adapter.py    # Portal nfse.gov.br (Emissor Nacional)
│   │
│   ├── api/                       # Camada HTTP
│   │   ├── routes/
│   │   │   ├── webhook.py         # POST /webhook — recebe eventos WhatsApp
│   │   │   └── health.py          # GET /health — status do servidor
│   │   ├── middleware.py          # Rate limit + logging de requests
│   │   └── dependencies.py        # Injeção de sessão DB e autenticação
│   │
│   ├── core/
│   │   ├── config.py              # Todas as variáveis de ambiente (pydantic-settings)
│   │   ├── exceptions.py          # Exceções do domínio
│   │   ├── logging.py             # Configuração do Loguru
│   │   └── security.py            # Verificação HMAC de webhook
│   │
│   ├── database/
│   │   ├── connection.py          # Engine async, WAL mode, create_tables/upgrade_schema
│   │   └── session.py             # Dependência de sessão para FastAPI
│   │
│   ├── integrations/
│   │   └── evolution/
│   │       ├── client.py          # Cliente HTTP da Evolution API
│   │       └── schemas.py         # Modelos Pydantic do webhook
│   │
│   ├── models/                    # Entidades SQLAlchemy
│   │   ├── user.py                # Usuário WhatsApp
│   │   ├── credential.py          # Credenciais do portal (senha criptografada)
│   │   ├── invoice.py             # Histórico de notas emitidas
│   │   └── session.py             # Estado da conversa por usuário
│   │
│   ├── repositories/              # CRUD isolado por entidade
│   ├── schemas/                   # DTOs Pydantic (entrada/saída da API)
│   │
│   ├── services/
│   │   ├── message_processor.py   # Máquina de estados da conversa WhatsApp
│   │   ├── nfse_service.py        # Orquestra emissão + persistência
│   │   └── user_service.py        # Gerenciamento de usuários e credenciais
│   │
│   ├── utils/
│   │   ├── crypto.py              # Criptografia Fernet para senhas
│   │   └── period.py              # Cálculo do período semanal
│   │
│   └── workers/
│       └── scheduler.py           # Job toda segunda às 9h (APScheduler)
│
├── data/                          # Banco SQLite (gitignore)
├── output/                        # PDFs, XMLs e screenshots (gitignore)
└── logs/                          # Logs da aplicação (gitignore)
```

---

## Endpoints da API

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Status do servidor e conexão WhatsApp |
| `POST` | `/webhook` | Recebe eventos da Evolution API |
| `GET` | `/docs` | Swagger UI (apenas com `DEBUG=true`) |

---

## ⚠️ Segurança

> **Nunca suba o `.env` para o GitHub.** Ele está no `.gitignore`, mas fique atento.

- **`ENCRYPTION_KEY`** — é a chave que criptografa as senhas dos usuários no banco. Se você perder essa chave, as senhas armazenadas ficam ilegíveis e todos os usuários precisarão recadastrar. Salve-a em um gerenciador de senhas (Bitwarden, 1Password, etc.).

- **`data/nfse.db`** — contém dados pessoais dos usuários (CNPJ, município, histórico de notas). Nunca suba esse arquivo para o GitHub. Ele está no `.gitignore`.

- **`output/`** — contém PDFs das notas fiscais dos usuários. Também no `.gitignore`.

- **`EVOLUTION_API_KEY`** — dá acesso total à sua instância WhatsApp. Trate como senha.

---

## Migração para PostgreSQL

Quando o volume de usuários crescer, troque apenas a `DATABASE_URL` no `.env`:

```
DATABASE_URL=postgresql+asyncpg://usuario:senha@host:5432/nfse
```

```bash
pip install asyncpg
make migrate
```

Nenhum outro código precisa ser alterado.
