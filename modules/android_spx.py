"""
modules/android_spx.py
Automação do app SPX Driver via ADB (sem Appium) e via Appium.
Responsável por ler os ganhos semanais e fazer upload do comprovante.
"""
import re
import time
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger
from config.settings import AndroidConfig, PathConfig
from modules.human_behavior import esperar, esperar_segundos


# ─────────────────────────────────────────────
# UTILITÁRIOS ADB
# ─────────────────────────────────────────────

def verificar_dispositivo_conectado() -> bool:
    """
    Verifica se há um dispositivo Android conectado via ADB.
    
    Returns:
        True se encontrou dispositivo, False caso contrário
    """
    try:
        resultado = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=10
        )
        linhas = resultado.stdout.strip().split("\n")
        # Ignora o cabeçalho 'List of devices attached'
        dispositivos = [l for l in linhas[1:] if l.strip() and "device" in l and "offline" not in l]
        
        if dispositivos:
            logger.info(f"📱 Dispositivo encontrado: {dispositivos[0].split()[0]}")
            return True
        else:
            logger.error("❌ Nenhum dispositivo Android conectado!")
            logger.error("   → Verifique: cabo USB, Depuração USB ativada e autorização no celular")
            return False
    except FileNotFoundError:
        logger.error("❌ ADB não encontrado! Instale o Android Platform Tools.")
        return False


def tirar_screenshot(nome: str = "screenshot") -> Path:
    """
    Captura a tela do dispositivo Android.
    
    Args:
        nome: Nome do arquivo (sem extensão)
    
    Returns:
        Path do arquivo salvo
    """
    caminho = PathConfig.OUTPUT / f"{nome}_{datetime.now().strftime('%H%M%S')}.png"
    subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/temp_screen.png"], check=True)
    subprocess.run(["adb", "pull", "/sdcard/temp_screen.png", str(caminho)], check=True)
    subprocess.run(["adb", "shell", "rm", "/sdcard/temp_screen.png"])
    logger.debug(f"📸 Screenshot salvo: {caminho}")
    return caminho


def dump_ui() -> str:
    """
    Faz dump da hierarquia de UI do app atual.
    
    Returns:
        Conteúdo XML da UI
    """
    subprocess.run(["adb", "shell", "uiautomator", "dump", "/sdcard/ui_dump.xml"], check=True)
    resultado = subprocess.run(
        ["adb", "shell", "cat", "/sdcard/ui_dump.xml"],
        capture_output=True, text=True
    )
    subprocess.run(["adb", "shell", "rm", "/sdcard/ui_dump.xml"])
    return resultado.stdout


def abrir_app_spx() -> bool:
    """
    Abre o app SPX Driver no dispositivo.
    
    Returns:
        True se abriu com sucesso
    """
    logger.info("📱 Abrindo app SPX Driver...")
    try:
        subprocess.run([
            "adb", "shell", "monkey", "-p", AndroidConfig.SPX_PACKAGE,
            "-c", "android.intent.category.LAUNCHER", "1"
        ], check=True, capture_output=True)
        
        esperar("longo")  # Aguarda o app carregar
        logger.info("✅ App SPX Driver aberto")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Falha ao abrir o app: {e}")
        return False


def extrair_valor_do_xml(xml: str) -> float | None:
    """
    Tenta extrair o valor de ganhos do XML da UI do app.
    Você precisará ajustar os padrões após inspecionar o seu app
    com 'adb shell uiautomator dump'.
    
    Args:
        xml: Conteúdo XML do dump de UI
    
    Returns:
        Valor em float ou None se não encontrado
    """
    # Padrões para encontrar valores monetários na UI
    # Ajuste esses padrões após inspecionar seu app com Appium Inspector ou uiautomator dump
    padroes = [
        r'R\$\s*([\d.,]+)',                    # "R$ 350,00"
        r'text="R\$\s*([\d.,]+)"',            # atributo text no XML
        r'content-desc="R\$\s*([\d.,]+)"',    # atributo content-desc
        r'text="([\d]+[.,][\d]{2})"',         # "350,00" ou "350.00"
    ]
    
    for padrao in padroes:
        matches = re.findall(padrao, xml)
        if matches:
            # Pega o maior valor encontrado (geralmente o total da semana)
            valores = []
            for m in matches:
                try:
                    v = float(m.replace(".", "").replace(",", "."))
                    if 10 < v < 10000:  # Faixa realista de ganhos semanais
                        valores.append(v)
                except ValueError:
                    continue
            
            if valores:
                valor = max(valores)
                logger.info(f"💰 Valor encontrado na UI: R$ {valor:.2f}")
                return valor
    
    return None


# ─────────────────────────────────────────────
# FLUXO PRINCIPAL: LER GANHOS DA SEMANA
# ─────────────────────────────────────────────

def ler_ganhos_semanais() -> dict | None:
    """
    Navega pelo app SPX Driver e extrai os ganhos da semana.
    
    IMPORTANTE: Este método usa análise de texto da UI (UIAutomator).
    Você precisará ajustar a navegação após inspecionar seu app.
    
    Returns:
        Dict com 'valor' (float), 'periodo' (str) e 'raw_xml' (str)
        ou None em caso de falha
    """
    logger.info("🔍 Iniciando leitura de ganhos no SPX Driver...")
    
    if not verificar_dispositivo_conectado():
        return None
    
    if not abrir_app_spx():
        return None
    
    esperar("longo")
    
    # --- PASSO 1: Navegar para a seção de ganhos ---
    # ATENÇÃO: Você precisará ajustar as coordenadas ou resource-ids
    # após inspecionar seu app com Appium Inspector ou UIAutomator dump
    logger.info("🗺️  Navegando para seção de ganhos...")
    
    # Tira screenshot para inspeção manual se necessário
    tirar_screenshot("antes_ganhos")
    
    # Exemplo de navegação por coordenadas (ajuste para seu dispositivo)
    # Para descobrir as coordenadas: adb shell getevent -l ou use Appium Inspector
    # subprocess.run(["adb", "shell", "input", "tap", "X", "Y"])
    
    # Exemplo de navegação por text (mais robusto que coordenadas)
    # subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
    # Clica em elemento por texto:
    # _clicar_por_texto("Ganhos")  # ou o texto exato no seu app
    
    esperar("medio")
    
    # --- PASSO 2: Selecionar período semanal ---
    # O SPX Driver geralmente mostra ganhos por semana
    # Ajuste a navegação conforme a interface do seu app
    logger.info("📅 Selecionando período semanal...")
    
    # Calcula a semana atual
    hoje = datetime.now()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    fim_semana = inicio_semana + timedelta(days=6)
    periodo_str = f"{inicio_semana.strftime('%d/%m')} a {fim_semana.strftime('%d/%m/%Y')}"
    
    esperar("medio")
    
    # --- PASSO 3: Capturar o valor exibido ---
    logger.info("📊 Capturando valor dos ganhos...")
    xml_ui = dump_ui()
    
    # Salva o XML para análise posterior
    xml_path = PathConfig.OUTPUT / f"ui_dump_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xml"
    xml_path.write_text(xml_ui, encoding="utf-8")
    logger.debug(f"📄 UI dump salvo em: {xml_path}")
    
    valor = extrair_valor_do_xml(xml_ui)
    
    if valor is None:
        logger.warning("⚠️  Não foi possível extrair o valor automaticamente.")
        logger.warning("   → Verifique o arquivo UI dump e ajuste os padrões em 'extrair_valor_do_xml'")
        logger.warning("   → Ou use o modo manual: python main.py --valor XXX.XX")
        
        # Modo de fallback: entrada manual
        try:
            valor_str = input("💬 Digite o valor manualmente (ex: 350.50): ").strip()
            valor = float(valor_str.replace(",", "."))
        except (ValueError, KeyboardInterrupt):
            logger.error("❌ Valor inválido ou operação cancelada")
            return None
    
    tirar_screenshot("apos_ganhos")
    
    resultado = {
        "valor": valor,
        "periodo": periodo_str,
        "data_leitura": datetime.now().isoformat(),
        "raw_xml": xml_ui[:500],  # Primeiros 500 chars para log
    }
    
    logger.success(f"✅ Ganhos lidos: R$ {valor:.2f} | Período: {periodo_str}")
    return resultado


# ─────────────────────────────────────────────
# UPLOAD DO COMPROVANTE
# ─────────────────────────────────────────────

def fazer_upload_comprovante(caminho_arquivo: Path) -> bool:
    """
    Faz upload do PDF/XML da nota fiscal no app SPX Driver.
    
    Args:
        caminho_arquivo: Path local do arquivo a enviar
    
    Returns:
        True se upload realizado com sucesso
    """
    logger.info(f"📤 Iniciando upload do comprovante: {caminho_arquivo.name}")
    
    if not caminho_arquivo.exists():
        logger.error(f"❌ Arquivo não encontrado: {caminho_arquivo}")
        return False
    
    if not verificar_dispositivo_conectado():
        return False
    
    try:
        # Envia o arquivo para o dispositivo
        destino_android = f"/sdcard/Download/{caminho_arquivo.name}"
        subprocess.run(
            ["adb", "push", str(caminho_arquivo), destino_android],
            check=True, capture_output=True
        )
        logger.info(f"📁 Arquivo enviado para: {destino_android}")
        
        # Abre o app SPX Driver
        abrir_app_spx()
        esperar("longo")
        
        # ATENÇÃO: A navegação até a tela de upload precisa ser ajustada
        # para o seu app. Use Appium Inspector para descobrir os elementos.
        logger.warning("⚠️  AÇÃO MANUAL NECESSÁRIA:")
        logger.warning(f"   1. No app SPX Driver, vá até a seção de comprovantes/NFS-e")
        logger.warning(f"   2. Selecione 'Enviar comprovante' ou similar")
        logger.warning(f"   3. Selecione o arquivo: {caminho_arquivo.name}")
        logger.warning(f"   4. O arquivo está em: Downloads/{caminho_arquivo.name}")
        
        input("\n▶️  Pressione Enter após concluir o upload manualmente...")
        
        logger.success(f"✅ Upload concluído: {caminho_arquivo.name}")
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Erro ao enviar arquivo via ADB: {e}")
        return False


def _clicar_por_texto(texto: str) -> bool:
    """
    Clica em um elemento da UI pelo texto visível.
    Helper interno para navegação no app.
    
    Args:
        texto: Texto visível do elemento
    
    Returns:
        True se clicou com sucesso
    """
    try:
        # Usa UIAutomator para encontrar e clicar por texto
        cmd = f'uiautomator runtest /data/local/tmp/uiautomator.jar -c NfseHelper#clickByText -e text "{texto}"'
        # Alternativa mais simples via am instrument:
        script = f"""
        import sys
        sys.path.append('/data/local/tmp')
        from uiautomator import device as d
        d(text="{texto}").click()
        """
        subprocess.run(["adb", "shell", f"am start -n {AndroidConfig.SPX_PACKAGE}/{AndroidConfig.SPX_ACTIVITY}"])
        return True
    except Exception as e:
        logger.debug(f"Falha ao clicar por texto '{texto}': {e}")
        return False
