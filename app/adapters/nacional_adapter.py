"""
app/adapters/nacional_adapter.py
Adapter para o portal Emissor Nacional (nfse.gov.br).
Seletores capturados via playwright codegen na emissão completa.
"""
import random
import re
import time
from datetime import date, datetime
from pathlib import Path

from loguru import logger
from playwright.sync_api import Page, sync_playwright

from app.adapters.base_adapter import BaseNfseAdapter, EmissionRequest, EmissionResult
from app.core.config import settings
from app.core.exceptions import NfseEmissionError

# ── timing ────────────────────────────────────────────────────────────────────

def _sleep(min_s: float, max_s: float) -> None:
    time.sleep(random.uniform(min_s, max_s))

def _curto() -> None:  _sleep(0.6, 1.2)
def _medio() -> None:  _sleep(1.8, 3.0)
def _longo() -> None:  _sleep(4.0, 6.0)


def _screenshot(page: Page, label: str) -> None:
    try:
        path = settings.screenshots_path / f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=str(path))
        logger.info("Screenshot: {}", path.name)
    except Exception:
        pass


# ── constantes SPX Driver ────────────────────────────────────────────────────

_PORTAL_URL   = "https://www.nfse.gov.br/EmissorNacional/Login"
_TOMADOR_CNPJ = "42.446.277/0001-92"
_COD_SERVICO  = "160201"           # fixo para motoristas SPX Driver
_MUNICIPIO_OPCAO_SUFIXO = "/SP"   # ajuste se houver municípios de outros estados


# ── adapter ───────────────────────────────────────────────────────────────────

class NacionalAdapter(BaseNfseAdapter):

    @property
    def municipality_key(self) -> str:
        return "nacional"

    def emit(self, request: EmissionRequest) -> EmissionResult:
        settings.ensure_directories()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=request.headless,
                slow_mo=request.slow_mo,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )
            page = context.new_page()
            try:
                self._login(page, request)
                _medio()
                self._abrir_emissao_completa(page)
                _medio()
                self._preencher_dados_gerais(page, request)
                _medio()
                self._preencher_servico(page, request)
                _medio()
                self._preencher_tomador_e_valor(page, request)
                _medio()
                self._emitir(page)
                _medio()
                numero = self._extrair_numero_nota(page)
                pdf_path = self._baixar_pdf(page, numero)
                return EmissionResult(
                    invoice_number=numero,
                    pdf_path=pdf_path,
                    xml_path=None,
                )
            except NfseEmissionError:
                raise
            except Exception as exc:
                _screenshot(page, "erro_geral")
                raise NfseEmissionError(str(exc), stage="unknown") from exc
            finally:
                context.close()
                browser.close()

    # ── 1. login ──────────────────────────────────────────────────────────────

    def _login(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Fazendo login...")
        try:
            page.goto(_PORTAL_URL, wait_until="networkidle", timeout=30_000)
            _medio()

            f_usuario = page.get_by_role("textbox", name="CPF/CNPJ")
            f_usuario.click()
            _curto()
            f_usuario.fill(request.username)
            _curto()

            f_senha = page.get_by_role("textbox", name="Senha")
            f_senha.click()
            _curto()
            f_senha.fill(request.password)
            _curto()

            page.get_by_role("button", name="Entrar").click()
            page.wait_for_load_state("networkidle", timeout=20_000)
            _medio()
            logger.info("Login OK")
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "login_erro")
            raise NfseEmissionError(f"Falha no login: {exc}", stage="login") from exc

    # ── 2. abrir emissão completa ─────────────────────────────────────────────

    def _abrir_emissao_completa(self, page: Page) -> None:
        logger.info("Abrindo emissão completa...")
        try:
            page.get_by_role("button").filter(has_text="Nova NFS-e").click(timeout=60_000)
            _medio()
            page.get_by_role("link", name="Emissão completa").click()
            page.wait_for_load_state("networkidle", timeout=15_000)
            _medio()
            _screenshot(page, "emissao_completa_aberta")
            logger.info("Formulário aberto")
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "nav_erro")
            raise NfseEmissionError(f"Falha ao abrir emissão: {exc}", stage="navigation") from exc

    # ── 3. dados gerais (data + local de prestação) ───────────────────────────

    def _preencher_dados_gerais(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Preenchendo dados gerais...")
        try:
            # Data de competência — dia atual no formato dd/mm/aaaa
            hoje = date.today().strftime("%d/%m/%Y")
            f_data = page.locator("#DataCompetencia")
            f_data.click()
            _curto()
            f_data.fill(hoje)
            # Fecha o datepicker pressionando Tab se ele abrir
            f_data.press("Tab")
            _curto()

            # Local de prestação — campo chosen dentro do painel #pnlLocalPrestacao
            municipio = request.municipio.split("/")[0].strip()
            painel = page.locator("#pnlLocalPrestacao")
            chosen = painel.locator("[class*='chosen-container']").first
            chosen.locator("a, .chosen-single").click()
            _curto()
            chosen.get_by_role("textbox").fill(municipio)
            _curto()
            page.locator(".chosen-results li").filter(has_text=municipio).first.click()
            _curto()

            _screenshot(page, "dados_gerais_ok")
            logger.info("Data {} | Município {}", hoje, _MUNICIPIO_OPCAO)

            self._avancar_se_existir(page)
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "dados_gerais_erro")
            raise NfseEmissionError(f"Falha nos dados gerais: {exc}", stage="dados_gerais") from exc

    # ── 4. serviço ────────────────────────────────────────────────────────────

    def _preencher_servico(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Preenchendo serviço — código {}", _COD_SERVICO)
        try:
            # Campo de busca do serviço — digita o código e aguarda autocomplete
            f_servico = page.locator("#ServicoPrestado_Descricao")
            f_servico.click()
            _curto()
            f_servico.fill(_COD_SERVICO)
            _medio()

            # Seleciona o primeiro resultado do autocomplete
            page.locator("ul.ui-autocomplete li.ui-menu-item").first.click()
            _curto()

            _screenshot(page, "servico_selecionado")
            logger.info("Serviço {} selecionado", _COD_SERVICO)

            self._avancar_se_existir(page)
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "servico_erro")
            raise NfseEmissionError(f"Falha ao preencher serviço: {exc}", stage="servico") from exc

    # ── 5. tomador + descrição + valor ────────────────────────────────────────

    def _preencher_tomador_e_valor(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Preenchendo tomador e valor R$ {:.2f}...", request.value)
        try:
            # CNPJ tomador (Shopee — fixo)
            f_cnpj = page.locator("#InscricaoCliente")
            f_cnpj.click()
            _curto()
            f_cnpj.fill(_TOMADOR_CNPJ)
            _curto()
            page.locator("#btn_InscricaoCliente_pesquisar").click()
            _medio()

            # Descrição
            descricao = (
                f"Nota referente aos serviços de entregas prestados "
                f"no período de {request.period}."
            )
            f_desc = page.locator("#Descricao")
            f_desc.click()
            _curto()
            f_desc.fill(descricao)
            _curto()

            # Valor
            valor_fmt = f"{request.value:.2f}".replace(".", ",")
            f_valor = page.locator("#Valores_ValorServico")
            f_valor.click()
            _curto()
            f_valor.fill(valor_fmt)
            _curto()

            _screenshot(page, "tomador_valor_ok")
            logger.info("Tomador + valor {} preenchidos", valor_fmt)

            self._avancar_se_existir(page)
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "tomador_valor_erro")
            raise NfseEmissionError(f"Falha ao preencher tomador/valor: {exc}", stage="tomador_valor") from exc

    # ── 6. emitir ─────────────────────────────────────────────────────────────

    def _emitir(self, page: Page) -> None:
        logger.info("Emitindo NFS-e...")
        try:
            page.get_by_role("button", name="Emitir").click()
            page.wait_for_load_state("networkidle", timeout=30_000)
            _longo()
            _screenshot(page, "pos_emissao")
            logger.info("NFS-e emitida")
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "emissao_erro")
            raise NfseEmissionError(f"Falha ao emitir: {exc}", stage="submit") from exc

    # ── 7. número da nota ─────────────────────────────────────────────────────

    def _extrair_numero_nota(self, page: Page) -> str | None:
        for selector in [
            "[id*='NumeroNfse']",
            "td:has-text('Número')",
            "strong:has-text('Número')",
            "span[id*='numero']",
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible():
                    nums = re.findall(r"\d+", el.text_content() or "")
                    if nums:
                        logger.info("Número da nota: {}", nums[0])
                        return nums[0]
            except Exception:
                continue
        logger.warning("Número da nota não encontrado")
        return None

    # ── 8. PDF ────────────────────────────────────────────────────────────────

    def _baixar_pdf(self, page: Page, numero: str | None) -> Path | None:
        logger.info("Baixando PDF...")
        sufixo = numero or datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = settings.pdf_path / f"nfse_{sufixo}.pdf"
        try:
            with page.expect_download(timeout=30_000) as dl:
                page.locator("a[href*='DANFSe'], a[href*='danfse' i]").first.click()
            dl.value.save_as(str(dest))
            logger.info("PDF: {}", dest.name)
            return dest
        except Exception as exc:
            _screenshot(page, "pdf_erro")
            logger.warning("Falha ao baixar PDF: {}", exc)
            return None

    # ── helper: avança no wizard se o botão existir ───────────────────────────

    @staticmethod
    def _avancar_se_existir(page: Page) -> None:
        try:
            btn = page.get_by_role("button", name=re.compile(r"Avançar|Próximo|Next", re.I))
            if btn.is_visible(timeout=2_000):
                btn.click()
                page.wait_for_load_state("networkidle", timeout=10_000)
                _medio()
        except Exception:
            pass  # sem botão Avançar — formulário é single-page
