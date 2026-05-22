FROM python:3.12-slim

WORKDIR /app

# curl para healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright Chromium + todas as dependências de sistema (libnss3, libgbm1, etc.)
RUN playwright install chromium --with-deps

# Código da aplicação
COPY . .

# Diretórios de runtime (criados também no startup pelo app)
RUN mkdir -p logs output/pdf output/xml output/screenshots

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Railway injeta PORT dinamicamente — usamos ${PORT:-8000} como fallback local
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
