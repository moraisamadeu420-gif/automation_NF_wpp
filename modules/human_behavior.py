"""
modules/human_behavior.py
Simula comportamento humano com esperas e ações aleatórias.
Reduz risco de detecção de automação por portais web.
"""
import time
import random
from loguru import logger
from config.settings import AutomacaoConfig


def esperar(tipo: str = "medio") -> None:
    """
    Aguarda um tempo aleatório que imita o comportamento humano.
    
    Args:
        tipo: 'curto', 'medio' ou 'longo'
    """
    delays = AutomacaoConfig.DELAYS.get(AutomacaoConfig.VELOCIDADE, AutomacaoConfig.DELAYS["normal"])
    min_t, max_t = delays.get(tipo, delays["medio"])
    tempo = round(random.uniform(min_t, max_t), 2)
    logger.debug(f"⏳ Aguardando {tempo}s ({tipo})")
    time.sleep(tempo)


def esperar_segundos(segundos: float, variacao: float = 0.3) -> None:
    """
    Aguarda um tempo específico com pequena variação aleatória.
    
    Args:
        segundos: Tempo base em segundos
        variacao: Variação percentual (0.3 = ±30%)
    """
    delta = segundos * variacao
    tempo = round(random.uniform(segundos - delta, segundos + delta), 2)
    time.sleep(tempo)


def digitar_como_humano(page, seletor: str, texto: str) -> None:
    """
    Digita texto simulando velocidade humana (caractere por caractere).
    
    Args:
        page: Página Playwright
        seletor: Seletor CSS do campo
        texto: Texto a digitar
    """
    page.click(seletor)
    esperar("curto")
    
    # Limpa o campo primeiro (Ctrl+A e Delete)
    page.press(seletor, "Control+a")
    esperar_segundos(0.2)
    page.press(seletor, "Delete")
    esperar_segundos(0.3)
    
    # Digita caractere por caractere com velocidade variável
    for char in texto:
        page.type(seletor, char, delay=random.randint(50, 180))
    
    esperar_segundos(0.5)


def mover_mouse_natural(page, seletor: str) -> None:
    """
    Move o mouse até um elemento de forma mais natural antes de clicar.
    
    Args:
        page: Página Playwright
        seletor: Seletor CSS do elemento
    """
    try:
        elemento = page.locator(seletor).first
        box = elemento.bounding_box()
        if box:
            # Move para perto do centro com pequena variação
            x = box["x"] + box["width"] / 2 + random.randint(-5, 5)
            y = box["y"] + box["height"] / 2 + random.randint(-3, 3)
            page.mouse.move(x, y)
            esperar_segundos(0.3)
    except Exception:
        pass  # Falha silenciosa, o clique normal será feito depois


def clicar_com_pausa(page, seletor: str) -> None:
    """
    Clica em um elemento após mover o mouse naturalmente.
    
    Args:
        page: Página Playwright
        seletor: Seletor CSS do elemento
    """
    mover_mouse_natural(page, seletor)
    page.click(seletor)
    esperar("curto")


def scroll_suave(page, direcao: str = "down", pixels: int = 300) -> None:
    """
    Faz scroll suave na página.
    
    Args:
        page: Página Playwright
        direcao: 'up' ou 'down'
        pixels: Quantidade de pixels para rolar
    """
    mult = 1 if direcao == "down" else -1
    # Divide em pequenos passos para parecer mais humano
    passos = random.randint(3, 6)
    passo_pixels = pixels // passos
    
    for _ in range(passos):
        page.mouse.wheel(0, mult * passo_pixels)
        esperar_segundos(random.uniform(0.1, 0.3))


def pausa_leitura(segundos: float = None) -> None:
    """
    Simula pausa de leitura humana.
    
    Args:
        segundos: Tempo de leitura (None = automático entre 2-5s)
    """
    if segundos is None:
        segundos = random.uniform(2, 5)
    logger.debug(f"👁️  Pausa de leitura: {segundos:.1f}s")
    time.sleep(segundos)


def confirmar_acao_manual(mensagem: str, detalhe: str = "") -> bool:
    """
    Pausa a execução e solicita confirmação manual do usuário.
    Essencial para o modo semi-automático antes de ações críticas.
    
    Args:
        mensagem: Mensagem de confirmação principal
        detalhe: Informações adicionais (ex: valor da nota)
    
    Returns:
        True se confirmado, False se cancelado
    """
    print("\n" + "=" * 60)
    print(f"⚠️  CONFIRMAÇÃO NECESSÁRIA")
    print("=" * 60)
    print(f"📋 {mensagem}")
    if detalhe:
        print(f"ℹ️  {detalhe}")
    print("-" * 60)
    
    while True:
        resposta = input("👉 Confirmar? [s/n]: ").strip().lower()
        if resposta in ("s", "sim", "y", "yes"):
            logger.info(f"✅ Usuário confirmou: {mensagem}")
            return True
        elif resposta in ("n", "nao", "não", "no"):
            logger.warning(f"❌ Usuário cancelou: {mensagem}")
            return False
        else:
            print("   Digite 's' para confirmar ou 'n' para cancelar.")
