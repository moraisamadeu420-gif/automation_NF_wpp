#!/bin/bash
# scripts/agendar_cron.sh
# Agenda a execução automática toda segunda-feira às 9h

# Detecta o Python do virtualenv
PROJETO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$PROJETO_DIR/venv/bin/python"

# Fallback para Python do sistema
if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN=$(which python3)
fi

MAIN_SCRIPT="$PROJETO_DIR/main.py"
LOG_DIR="$PROJETO_DIR/logs"

echo "📁 Diretório do projeto: $PROJETO_DIR"
echo "🐍 Python: $PYTHON_BIN"

# Cria a entrada do crontab
# Formato: minuto hora * * dia_semana (1 = segunda-feira)
# 0 9 * * 1 = toda segunda-feira às 09:00
CRON_ENTRY="0 9 * * 1 cd $PROJETO_DIR && $PYTHON_BIN $MAIN_SCRIPT >> $LOG_DIR/cron_\$(date +\%Y\%m\%d).log 2>&1"

# Verifica se já existe entrada
(crontab -l 2>/dev/null | grep -q "spx_nfse") && {
    echo "⚠️  Agendamento já existe no crontab. Removendo anterior..."
    crontab -l 2>/dev/null | grep -v "spx_nfse" | crontab -
}

# Adiciona nova entrada
(crontab -l 2>/dev/null; echo "# spx_nfse - Automação NFS-e SPX Driver"; echo "$CRON_ENTRY") | crontab -

echo ""
echo "✅ Agendamento configurado!"
echo "   ⏰ Execução: toda segunda-feira às 09:00"
echo ""
echo "📋 Para verificar: crontab -l"
echo "❌ Para remover:   crontab -l | grep -v 'spx_nfse' | crontab -"
echo ""
echo "💡 Para testar agora: $PYTHON_BIN $MAIN_SCRIPT --semi-auto"
