.PHONY: install dev start migrate revision stamp health lint test \
        docker-build docker-up docker-down docker-logs docker-shell \
        webhook-test scheduler-test reminder-test cleanup-test mp-payment-test expire-sub clean

PYTHON  := python3
VENV    := .venv
PIP     := $(VENV)/bin/pip
UV      := $(VENV)/bin/uvicorn
ALB     := $(VENV)/bin/alembic
HOST    ?= http://localhost:8000
NUMBER  ?= 5541999000001
TEXT    ?= oi

# ── Local development ────────────────────────────────────────────────────────

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(VENV)/bin/playwright install chromium

dev:
	$(UV) main:app --host 0.0.0.0 --port 8000 --reload --log-level debug

start:
	$(UV) main:app --host 0.0.0.0 --port 8000 --workers 1

# ── Banco de dados / Alembic ─────────────────────────────────────────────────

migrate:
	$(ALB) upgrade head

# Cria nova migration (ex: make revision msg="add column foo")
revision:
	$(ALB) revision --autogenerate -m "$(msg)"

# BANCO EXISTENTE (SQLite já em uso): marca como atualizado sem rodar a migration
# Execute UMA VEZ ao adotar Alembic em banco que já existe
stamp:
	$(ALB) stamp 0001

# ── Docker ───────────────────────────────────────────────────────────────────

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f app

docker-shell:
	docker compose exec app bash

# Sobe e mostra logs (útil para primeiro start)
docker-start:
	docker compose up --build

# ── Utilitários / Testes ─────────────────────────────────────────────────────

health:
	curl -s $(HOST)/health | python3 -m json.tool

lint:
	$(VENV)/bin/ruff check app/ main.py
	$(VENV)/bin/mypy app/ main.py --ignore-missing-imports

test:
	$(VENV)/bin/pytest tests/ -v

webhook-test:
	@echo "Simulando mensagem: '$(TEXT)' do número $(NUMBER)..."
	@curl -s -X POST $(HOST)/webhook \
		-H "Content-Type: application/json" \
		-d "{\"event\":\"MESSAGES_UPSERT\",\"instance\":\"spx-nfse\",\"data\":{\"key\":{\"remoteJid\":\"$(NUMBER)@s.whatsapp.net\",\"fromMe\":false,\"id\":\"TEST-$(shell date +%s)\"},\"message\":{\"conversation\":\"$(TEXT)\"},\"messageType\":\"conversation\",\"pushName\":\"Driver Teste\"}}" \
		| python3 -m json.tool

scheduler-test:
	@echo "Disparando weekly prompt..."
	@curl -s -X POST $(HOST)/webhook/trigger-weekly-prompt | python3 -m json.tool

reminder-test:
	@echo "Disparando lembrete de expiração..."
	@curl -s -X POST $(HOST)/webhook/trigger-subscription-reminder | python3 -m json.tool

cleanup-test:
	@echo "Disparando limpeza de usuários cancelados..."
	@curl -s -X POST $(HOST)/webhook/trigger-cleanup | python3 -m json.tool

USER_ID ?= 1
mp-payment-test:
	@echo "Simulando pagamento aprovado para user_id=$(USER_ID)..."
	@curl -s -X POST $(HOST)/webhook/mercadopago \
		-H "Content-Type: application/json" \
		-d "{\"type\":\"payment\",\"data\":{\"id\":\"TEST-PAY-$(shell date +%s)\"},\"external_reference\":\"$(USER_ID)\"}" \
		| python3 -m json.tool

expire-sub:
	@echo "Expirando assinatura do número $(NUMBER)..."
	@sqlite3 data/nfse.db "UPDATE users SET subscription_expires_at='2000-01-01 00:00:00' WHERE whatsapp_number='$(NUMBER)';"
	@echo "Pronto."

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
