"""
modules/nfse_web.py
Automação do portal NFS-e (Emissor Nacional) via Playwright.
"""
from pathlib import Path
from datetime import datetime
from loguru import logger
from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PWTimeoutError
from config.settings import NfseConfig, TomadorConfig, ServicoConfig, AutomacaoConfig, PathConfig
from modules.human_behavior import esperar, esperar_segundos, pausa_leitura, confirmar_acao_manual


def salvar_screenshot_erro(page, nome="erro"):
    try:
        caminho = PathConfig.LOGS / f"{nome}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=str(caminho))
    except Exception:
        pass


def fazer_login(page):
    logger.info("🔐 Realizando login no portal NFS-e...")
    try:
        page.goto(NfseConfig.URL, wait_until="networkidle", timeout=30000)
        esperar("medio")
        page.get_by_role("textbox", name="CPF/CNPJ").click()
        esperar("curto")
        page.get_by_role("textbox", name="CPF/CNPJ").fill(NfseConfig.USUARIO)
        esperar("curto")
        page.get_by_role("textbox", name="Senha").click()
        esperar("curto")
        page.get_by_role("textbox", name="Senha").fill(NfseConfig.SENHA)
        esperar("curto")
        page.get_by_role("button", name="Entrar").click()
        page.wait_for_load_state("networkidle", timeout=20000)
        esperar("medio")
        logger.success("✅ Login realizado!")
        return True
    except Exception as e:
        logger.error(f"❌ Falha no login: {e}")
        salvar_screenshot_erro(page, "login_erro")
        return False


def navegar_para_emissao(page):
    logger.info("🗺️  Navegando para emissão de NFS-e...")
    try:
        page.get_by_role("button").filter(has_text="Nova NFS-e").click()
        esperar("medio")
        page.get_by_role("link", name="Emissão simplificada").click()
        page.wait_for_load_state("networkidle", timeout=15000)
        esperar("medio")
        logger.success("✅ Tela de emissão aberta!")
        return True
    except Exception as e:
        logger.error(f"❌ Erro ao navegar: {e}")
        salvar_screenshot_erro(page, "nav_erro")
        return False


def preencher_formulario(page, dados):
    logger.info("📝 Preenchendo formulário...")
    valor = dados["valor"]
    periodo = dados.get("periodo", datetime.now().strftime("semana de %d/%m/%Y"))
    try:
        page.locator("#InscricaoCliente").click()
        esperar("curto")
        page.locator("#InscricaoCliente").fill(TomadorConfig.CNPJ)
        esperar("curto")
        page.locator("#btn_InscricaoCliente_pesquisar").click()
        esperar("medio")

        page.locator("#IdServicoFavorito_chosen a").filter(has_text="Selecione...").click()
        esperar("curto")
        page.locator("#IdServicoFavorito_chosen").get_by_text(ServicoConfig.NOME_FAVORITO).click()
        esperar("curto")

        page.locator("#btnDescricao").click()
        esperar("medio")
        descricao = f"Nota referente aos serviços de entregas prestados no período de {periodo}."
        page.locator("#Descricao").click()
        esperar("curto")
        page.locator("#Descricao").fill(descricao)
        esperar("curto")

        valor_formatado = f"{valor:.2f}".replace(".", ",")
        page.locator("#ValorServico").click()
        esperar("curto")
        page.locator("#ValorServico").fill(valor_formatado)
        esperar("curto")

        page.locator("#InfoComplementar_CodigoMunicipio_chosen a").filter(has_text="Selecione...").click()
        esperar("curto")
        page.locator("#InfoComplementar_CodigoMunicipio_chosen").get_by_role("textbox").fill("campinas")
        esperar("curto")
        page.locator("#InfoComplementar_CodigoMunicipio_chosen").get_by_text("Campinas/SP").click()
        esperar("medio")

        logger.success("✅ Formulário preenchido!")
        return True
    except Exception as e:
        logger.error(f"❌ Erro ao preencher formulário: {e}")
        salvar_screenshot_erro(page, "formulario_erro")
        return False


def confirmar_e_emitir(page, valor):
    logger.info("🚀 Preparando para emitir...")
    if AutomacaoConfig.SEMI_AUTO:
        screenshot = PathConfig.LOGS / f"revisao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=str(screenshot))
        logger.info(f"📸 Revise o formulário: {screenshot}")
        confirmado = confirmar_acao_manual(
            mensagem=f"Emitir NFS-e no valor de R$ {valor:.2f}?",
            detalhe=f"Tomador: {TomadorConfig.RAZAO_SOCIAL}"
        )
        if not confirmado:
            logger.warning("⚠️  Emissão cancelada pelo usuário")
            return False
    try:
        page.get_by_role("button", name="Emitir").click()
        page.wait_for_load_state("networkidle", timeout=30000)
        esperar("longo")
        logger.success("✅ NFS-e emitida!")
        return True
    except Exception as e:
        logger.error(f"❌ Erro ao emitir: {e}")
        salvar_screenshot_erro(page, "emissao_erro")
        return False


def baixar_pdf(page, numero_nota=None):
    logger.info("📥 Baixando PDF da nota...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sufixo = numero_nota if numero_nota else timestamp
    nome_arquivo = PathConfig.PDF / f"nfse_{sufixo}.pdf"
    try:
        with page.expect_download(timeout=30000) as dl:
            page.locator("a[href*='DANFSe']").first.click()
        dl.value.save_as(str(nome_arquivo))
        logger.success(f"✅ PDF salvo: {nome_arquivo.name}")
        return nome_arquivo
    except Exception as e:
        logger.error(f"❌ Erro ao baixar PDF: {e}")
        salvar_screenshot_erro(page, "download_erro")
        return None


def extrair_numero_nota(page):
    import re
    for seletor in ["td:has-text('Número')", "span[id*='numero']", "strong:has-text('Número')"]:
        try:
            el = page.locator(seletor).first
            if el.is_visible():
                nums = re.findall(r'\d+', el.text_content())
                if nums:
                    return nums[0]
        except Exception:
            continue
    return None


def executar_emissao_nfse(dados_ganhos):
    logger.info("🏁 Iniciando fluxo de emissão de NFS-e...")
    PathConfig.garantir_pastas()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=AutomacaoConfig.HEADLESS,
            slow_mo=300,
            args=["--disable-blink-features=AutomationControlled"]
        )
        contexto = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            accept_downloads=True,
        )
        page = contexto.new_page()
        try:
            if not fazer_login(page): return None
            pausa_leitura(2)
            if not navegar_para_emissao(page): return None
            esperar("medio")
            if not preencher_formulario(page, dados_ganhos): return None
            esperar("medio")
            if not confirmar_e_emitir(page, dados_ganhos["valor"]): return None
            esperar("medio")
            numero_nota = extrair_numero_nota(page)
            if numero_nota:
                logger.info(f"🔢 Número da nota: {numero_nota}")
            pdf_path = baixar_pdf(page, numero_nota)
            return {
                "numero_nota": numero_nota,
                "valor": dados_ganhos["valor"],
                "periodo": dados_ganhos.get("periodo", ""),
                "pdf": pdf_path,
                "xml": None,
                "data_emissao": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"❌ Erro geral: {e}")
            salvar_screenshot_erro(page, "erro_geral")
            return None
        finally:
            esperar("curto")
            contexto.close()
            browser.close()
