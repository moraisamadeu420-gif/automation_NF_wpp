# 🚀 SPX Driver → NFS-e Automação

Automação do fluxo semanal: leitura de ganhos no SPX Driver Android → emissão de NFS-e → upload do comprovante.

## 📁 Estrutura do Projeto

```
spx_nfse_automacao/
├── config/
│   └── settings.py          # Configurações centralizadas
├── modules/
│   ├── android_spx.py       # Automação Android (ADB/Appium)
│   ├── nfse_web.py          # Automação web NFS-e (Playwright)
│   ├── file_manager.py      # Gerenciamento de arquivos (PDF/XML)
│   ├── history.py           # Histórico e exportação CSV/Excel
│   └── human_behavior.py    # Esperas e comportamento humano
├── logs/                    # Logs de execução
├── output/
│   ├── notas/               # Notas organizadas por semana
│   ├── pdf/                 # PDFs baixados
│   └── xml/                 # XMLs baixados
├── data/
│   └── historico.csv        # Histórico de notas emitidas
├── scripts/
│   └── agendar_cron.sh      # Script para agendar no cron
├── tests/
│   └── test_modules.py      # Testes básicos
├── main.py                  # Ponto de entrada principal
├── .env.example             # Exemplo de variáveis de ambiente
├── requirements.txt         # Dependências Python
└── README.md
```

## ⚙️ Pré-requisitos do Sistema

- Python 3.10+
- Node.js 18+ (para Playwright)
- Android Debug Bridge (ADB)
- Appium 2.x (opcional, mas recomendado)
- Celular Android com depuração USB ativada

## 🔧 Instalação Passo a Passo

### 1. Clone e configure o ambiente Python

```bash
# Crie e ative o virtualenv
python -m venv venv
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# Instale as dependências
pip install -r requirements.txt

# Instale os navegadores do Playwright
playwright install chromium
```

### 2. Configure as variáveis de ambiente

```bash
cp .env.example .env
# Edite o .env com seus dados reais
nano .env
```

### 3. Instale o ADB (Android Debug Bridge)

**Ubuntu/Debian:**

```bash
sudo apt-get install adb
```

**Windows:**
Baixe o Android Platform Tools: https://developer.android.com/tools/releases/platform-tools

**Mac:**

```bash
brew install android-platform-tools
```

### 4. Configure o celular Android para depuração USB

1. Vá em **Configurações → Sobre o telefone**
2. Toque **7x em "Número da versão"** para ativar o Modo Desenvolvedor
3. Vá em **Configurações → Opções do desenvolvedor**
4. Ative **Depuração USB**
5. Conecte o cabo USB no PC
6. No celular, **autorize o PC** quando aparecer o popup

### 5. Verifique a conexão ADB

```bash
adb devices
# Deve aparecer: Lista of devices attached
# XXXXXXXXXX   device
```

### 6. (Opcional) Instale o Appium para automação mais robusta

```bash
npm install -g appium
appium driver install uiautomator2
appium &   # Inicia o servidor Appium em background
```

## 🔍 Como Capturar Elementos do App Android (SPX Driver)

### Via ADB UIAutomator Dump (sem Appium)

```bash
# Com o app SPX Driver aberto na tela desejada:
adb shell uiautomator dump /sdcard/screen.xml
adb pull /sdcard/screen.xml .
cat screen.xml | grep -i "text\|resource-id\|content-desc"
```

### Via Appium Inspector (recomendado)

1. Baixe: https://github.com/appium/appium-inspector/releases
2. Configure a conexão:
   - Remote Host: `127.0.0.1`
   - Remote Port: `4723`
   - Capabilities:
     ```json
     {
       "platformName": "Android",
       "appium:automationName": "UiAutomator2",
       "appium:deviceName": "SEU_DISPOSITIVO",
       "appium:appPackage": "com.shopee.spxdriver",
       "appium:appActivity": ".MainActivity",
       "appium:noReset": true
     }
     ```
3. Clique em **Start Session** e inspecione os elementos

### Via ADB Screenshot + UI Viewer

```bash
adb exec-out screencap -p > screenshot.png
# Use o Android Studio ou o uiautomatorviewer para inspecionar
```

## 🌐 Como Localizar Elementos no Site da NFS-e

### Método recomendado com Playwright

```python
# Abra o Playwright em modo headful para inspecionar
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=500)
    page = browser.new_page()
    page.goto("https://SEU_MUNICIPIO.nfse.gov.br")

    # Use o DevTools do navegador para inspecionar elementos
    # F12 → Inspector → clique no elemento → copie o seletor
    input("Pressione Enter para fechar...")
    browser.close()
```

### Seletores mais comuns encontrados em portais NFS-e

```python
# Campos típicos (adapte ao seu município)
SELETORES = {
    "login_cpf_cnpj": "input[name='usuario']",
    "login_senha": "input[type='password']",
    "btn_login": "button[type='submit']",
    "menu_emitir": "a:has-text('Emitir')",
    "campo_valor": "input[id*='valor']",
    "campo_descricao": "textarea[id*='descricao']",
    "btn_confirmar": "button:has-text('Confirmar')",
    "btn_download_pdf": "a:has-text('PDF')",
    "btn_download_xml": "a:has-text('XML')",
}
```

## ▶️ Como Executar

```bash
# Execução completa automática
python main.py

# Modo semi-automático (pausa para confirmação)
python main.py --semi-auto

# Apenas ler ganhos do SPX (sem emitir nota)
python main.py --apenas-leitura

# Apenas emitir nota com valor manual
python main.py --valor 350.00

# Exportar histórico para Excel
python main.py --exportar-excel
```

## 🕐 Agendar Execução Toda Segunda-feira

```bash
# Execute o script de agendamento
chmod +x scripts/agendar_cron.sh
./scripts/agendar_cron.sh

# Ou adicione manualmente ao crontab:
crontab -e
# Adicione a linha:
# 0 9 * * 1 /caminho/para/venv/bin/python /caminho/para/main.py >> /caminho/para/logs/cron.log 2>&1
```

## 📊 Histórico e Exportação

O histórico fica salvo em `data/historico.csv` e pode ser exportado:

```bash
python main.py --exportar-excel   # Gera data/historico_notas.xlsx
python main.py --exportar-csv     # Atualiza data/historico.csv
```

## 🛡️ Boas Práticas de Segurança

- Nunca versione o arquivo `.env` (já está no `.gitignore`)
- Use senhas únicas para automação
- Mantenha logs de auditoria
- Sempre confirme manualmente antes do envio final
- Prefira modo `--semi-auto` até ter confiança total no fluxo
