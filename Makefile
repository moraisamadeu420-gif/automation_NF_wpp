.PHONY: install dev start migrate revision health lint webhook-test scheduler-test

PYTHON  := python3
VENV    := .venv
PIP     := $(VENV)/bin/pip
UV      := $(VENV)/bin/uvicorn
ALB     := $(VENV)/bin/alembic
HOST    ?= http://localhost:8000
NUMBER  ?= 5541999000001
TEXT    ?= oi

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(VENV)/bin/playwright install chromium

dev:
	$(UV) main:app --host 0.0.0.0 --port 8000 --reload --log-level debug

start:
	$(UV) main:app --host 0.0.0.0 --port 8000 --workers 1

migrate:
	$(ALB) upgrade head

revision:
	$(ALB) revision --autogenerate -m "$(msg)"

health:
	curl -s http://localhost:8000/health | python3 -m json.tool

lint:
	$(VENV)/bin/ruff check app/ main.py
	$(VENV)/bin/mypy app/ main.py --ignore-missing-imports

webhook-test:
	@echo "Simulando mensagem de texto: '$(TEXT)' do numero $(NUMBER)..."
	@curl -s -X POST $(HOST)/webhook \
		-H "Content-Type: application/json" \
		-d "{\"event\":\"MESSAGES_UPSERT\",\"instance\":\"spx-nfse\",\"data\":{\"key\":{\"remoteJid\":\"$(NUMBER)@s.whatsapp.net\",\"fromMe\":false,\"id\":\"TEST-$(shell date +%s)\"},\"message\":{\"conversation\":\"$(TEXT)\"},\"messageType\":\"conversation\",\"pushName\":\"Driver Teste\"}}" \
		| python3 -m json.tool

scheduler-test:
	@echo "Disparando o job do scheduler manualmente..."
	@curl -s -X POST $(HOST)/webhook/trigger-weekly-prompt | python3 -m json.tool

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
