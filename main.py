"""
main.py
Ponto de entrada principal da automação SPX Driver → NFS-e.

Uso:
    python main.py                    # Fluxo completo
    python main.py --semi-auto        # Pausa para confirmação manual
    python main.py --valor 350.00     # Pula leitura Android, usa valor fixo
    python main.py --apenas-leitura   # Só lê ganhos, não emite nota
    python main.py --exportar-excel   # Exporta histórico para Excel
    python main.py --historico        # Mostra histórico no terminal
"""
import sys
import argparse
from datetime import datetime
from pathlib import Path
from loguru import logger

# Configura paths para importação dos módulos
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import PathConfig, AutomacaoConfig
from modules.android_spx import fazer_upload_comprovante
from modules.nfse_web import executar_emissao_nfse
from modules.history import registrar_nota, registrar_falha, exportar_excel, exibir_resumo_terminal
from modules.human_behavior import confirmar_acao_manual


# ─────────────────────────────────────────────
# CONFIGURAÇÃO DE LOGS
# ─────────────────────────────────────────────

def configurar_logs():
    """Configura o sistema de logs com rotação de arquivos."""
    PathConfig.garantir_pastas()
    
    # Remove handler padrão
    logger.remove()
    
    # Log no terminal (colorido, apenas INFO+)
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="INFO",
        colorize=True,
    )
    
    # Log em arquivo (todos os níveis, rotação diária)
    log_file = PathConfig.LOGS / f"automacao_{datetime.now().strftime('%Y%m%d')}.log"
    logger.add(
        str(log_file),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
        level="DEBUG",
        rotation="1 day",
        retention="30 days",
        encoding="utf-8",
    )
    
    logger.debug(f"📝 Logs salvos em: {log_file}")


# ─────────────────────────────────────────────
# FLUXO PRINCIPAL
# ─────────────────────────────────────────────

def fluxo_completo(valor_manual: float = None, semi_auto: bool = None) -> bool:
    """
    Executa o fluxo completo: Leitura → Emissão → Download → Upload.
    
    Args:
        valor_manual: Se informado, pula a leitura Android e usa este valor
        semi_auto: Override do modo semi-automático (None = usa .env)
    
    Returns:
        True se completou com sucesso
    """
    logger.info("=" * 60)
    logger.info("🚀 INICIANDO AUTOMAÇÃO SPX DRIVER → NFS-e")
    logger.info(f"📅 Data/hora: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    logger.info("=" * 60)
    
    # Override do modo semi-auto via parâmetro
    if semi_auto is not None:
        AutomacaoConfig.SEMI_AUTO = semi_auto
    
    dados_ganhos = None
    
    # ── ETAPA 1: Leitura de ganhos ──────────────────────────
    if valor_manual is not None:
        logger.info(f"💰 Usando valor manual: R$ {valor_manual:.2f}")
        dados_ganhos = {
            "valor": valor_manual,
            "periodo": f"semana de {datetime.now().strftime('%d/%m/%Y')}",
            "data_leitura": datetime.now().isoformat(),
        }
    else:
        logger.info("📱 ETAPA 1/4: Lendo ganhos no SPX Driver...")
        from modules.ocr_spx import solicitar_upload_imagem, ler_ganhos_do_print
        caminho_print = solicitar_upload_imagem()
        if not caminho_print:
            logger.error("❌ Nenhuma imagem fornecida. Abortando.")
            return False
        dados_ganhos = ler_ganhos_do_print(caminho_print)
        if not dados_ganhos:
            logger.error("❌ Não foi possível ler o valor da imagem. Abortando.")
            return False
        
        if not dados_ganhos:
            logger.error("❌ Falha na leitura dos ganhos. Abortando.")
            logger.info("💡 Dica: Use --valor XXX.XX para especificar o valor manualmente")
            return False
    
    valor = dados_ganhos["valor"]
    logger.info(f"✅ Valor a ser emitido: R$ {valor:.2f}")
    
    # ── ETAPA 2: Emissão da NFS-e ──────────────────────────
    logger.info("🌐 ETAPA 2/4: Emitindo NFS-e no portal...")
    resultado = executar_emissao_nfse(dados_ganhos)
    
    if not resultado:
        logger.error("❌ Falha na emissão da NFS-e.")
        registrar_falha(valor, dados_ganhos.get("periodo", ""), "Falha na emissão automática")
        return False
    
    # ── ETAPA 3: Download e histórico ──────────────────────
    logger.info("💾 ETAPA 3/4: Salvando histórico...")
    registrar_nota(resultado)
    
    pdf_path = resultado.get("pdf")
    xml_path = resultado.get("xml")
    
    if pdf_path:
        logger.success(f"📄 PDF: {pdf_path}")
    if xml_path:
        logger.success(f"📄 XML: {xml_path}")
        # Envia PDF via WhatsApp
    if pdf_path:
        from modules.whatsapp import conectar_whatsapp, enviar_pdf
        if conectar_whatsapp():
            enviar_pdf(Path(pdf_path), resultado.get("numero_nota"), valor)
    
    # ── ETAPA 4: Upload no SPX Driver ──────────────────────
    # ── ETAPA 4: Envio via WhatsApp ────────────────────────
    logger.info("📤 ETAPA 4/4: Enviando PDF via WhatsApp...")
    if pdf_path:
        from modules.whatsapp import conectar_whatsapp, enviar_pdf
        if conectar_whatsapp():
            enviar_pdf(
                Path(pdf_path),
                numero_nota=resultado.get("numero_nota"),
                valor=valor
            )
    else:
        logger.warning("⚠️  Sem PDF para enviar. Faça o upload manualmente.")
        
        # ── RESUMO FINAL ────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.success("🎉 AUTOMAÇÃO CONCLUÍDA COM SUCESSO!")
    logger.info(f"   📋 Nota Nº: {resultado.get('numero_nota', 'N/A')}")
    logger.info(f"   💰 Valor: R$ {valor:.2f}")
    logger.info(f"   📅 Período: {resultado.get('periodo', 'N/A')}")
    logger.info("=" * 60)
    
    return True


# ─────────────────────────────────────────────
# CLI (ARGUMENTOS DE LINHA DE COMANDO)
# ─────────────────────────────────────────────

def main():
    configurar_logs()
    
    parser = argparse.ArgumentParser(
        description="🚚 Automação SPX Driver → NFS-e",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python main.py                    Fluxo completo automático
  python main.py --semi-auto        Pausa antes de emitir para revisão
  python main.py --valor 350.00     Usa valor fixo (pula leitura Android)
  python main.py --apenas-leitura   Só lê ganhos do SPX, não emite
  python main.py --exportar-excel   Exporta histórico para Excel
  python main.py --historico        Mostra histórico no terminal
        """
    )
    
    parser.add_argument(
        "--valor", type=float, default=None,
        help="Valor manual da nota (pula leitura do Android)"
    )
    parser.add_argument(
        "--semi-auto", action="store_true",
        help="Pausa para confirmação manual antes de emitir"
    )
    parser.add_argument(
        "--apenas-leitura", action="store_true",
        help="Apenas lê os ganhos, não emite a nota"
    )
    parser.add_argument(
        "--exportar-excel", action="store_true",
        help="Exporta histórico para Excel e encerra"
    )
    parser.add_argument(
        "--historico", action="store_true",
        help="Mostra histórico de notas no terminal"
    )
    
    args = parser.parse_args()
    
    # Modo: exportar Excel
    if args.exportar_excel:
        logger.info("📊 Exportando histórico para Excel...")
        xlsx = exportar_excel()
        if xlsx:
            logger.success(f"✅ Excel gerado: {xlsx}")
        return
    
    # Modo: histórico no terminal
    if args.historico:
        exibir_resumo_terminal()
        return
    
    # Modo: apenas leitura
    if args.apenas_leitura:
        logger.info("🔍 Modo apenas leitura...")
        from modules.ocr_spx import solicitar_upload_imagem, ler_ganhos_do_print
        caminho_print = solicitar_upload_imagem()
        dados = ler_ganhos_do_print(caminho_print) if caminho_print else None
        if dados:
            logger.success(f"💰 Ganhos da semana: R$ {dados['valor']:.2f}")
            logger.info(f"   Período: {dados['periodo']}")
        return
    
    # Modo: fluxo completo
    semi_auto = args.semi_auto or AutomacaoConfig.SEMI_AUTO
    sucesso = fluxo_completo(
        valor_manual=args.valor,
        semi_auto=semi_auto,
    )
    
    sys.exit(0 if sucesso else 1)


if __name__ == "__main__":
    main()
