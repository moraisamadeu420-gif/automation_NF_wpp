"""
app/integrations/anthropic/client.py
Claude-powered intent classification and FAQ answering for the WhatsApp bot.

Used in two situations:
  1. IDLE state — user sends something that doesn't match any known command.
     Claude maps it to a menu option (1-7) or answers if it's a question.
  2. ONBOARDING_WELCOME — user responds to "Quer começar?" with something
     other than SIM/NÃO. Claude decides if it's affirmative or negative.
"""
import json

from anthropic import AsyncAnthropic
from loguru import logger

from app.core.config import settings

def _build_system_prompt() -> str:
    return (
        "Você é o assistente virtual do Bot NFSe, um serviço que automatiza a emissão de "
        "Nota Fiscal de Serviço Eletrônico (NFS-e) via WhatsApp para prestadores de serviços "
        "brasileiros (MEI, autônomo com CNPJ, etc.).\n\n"
        "━━━ O QUE O BOT FAZ ━━━\n"
        "• Emite NFS-e automaticamente pelo portal Emissor Nacional (nfse.gov.br)\n"
        "• Toda segunda-feira envia lembrete para o usuário informar o valor dos ganhos\n"
        "• O usuário só informa o valor — o bot preenche e envia a nota\n"
        "• Guarda histórico completo de todas as notas emitidas\n\n"
        "━━━ REQUISITOS ━━━\n"
        "• CNPJ de pessoa jurídica ativo (MEI ou empresa)\n"
        "• Senha de acesso direto no portal nfse.gov.br (login CNPJ + Senha, NÃO Gov.br)\n"
        "  - Se só tem acesso pelo Gov.br, precisa criar senha direta no portal primeiro\n"
        "• Estar cadastrado no Emissor Nacional de NFS-e do município\n\n"
        "━━━ PLANOS E PREÇOS ━━━\n"
        f"• Trial grátis de {settings.trial_days} dias (sem cartão)\n"
        f"• Assinatura mensal: R$ {settings.subscription_price:.2f}".replace(".", ",") + "\n"
        "• Cancelamento a qualquer momento\n\n"
        "━━━ MENU PRINCIPAL ━━━\n"
        "1 — Emitir NFS-e: emite uma nota agora (o usuário informa o valor)\n"
        "2 — Histórico de notas: lista as notas emitidas anteriormente\n"
        "3 — Atualizar dados: altera CNPJ, senha ou município cadastrados\n"
        "4 — Suporte: contato direto com atendente humano\n"
        "5 — Assinaturas: assinar, renovar ou cancelar plano\n"
        "6 — Somar notas: total faturado no período\n"
        "7 — Configurar horário: muda o horário do lembrete semanal de segunda-feira\n\n"
        "━━━ SUA TAREFA ━━━\n"
        "Analise a mensagem do usuário e responda SOMENTE com um JSON válido, sem texto extra.\n\n"
        "Formatos possíveis:\n"
        "1. Intenção clara de usar uma opção do menu:\n"
        '   {"action": "menu", "option": "N"}   — N = número de 1 a 7\n\n'
        "2. Pergunta ou dúvida que você consegue responder:\n"
        '   {"action": "faq", "answer": "resposta aqui"}\n'
        "   — Resposta em português brasileiro, amigável, máximo 3 parágrafos curtos\n"
        "   — Nunca invente funcionalidades; se não souber, indique o suporte (opção 4)\n\n"
        "3. Intenção de início do onboarding (resposta afirmativa a \"Quer começar?\"):\n"
        '   {"action": "onboarding_yes"}\n\n'
        "4. Intenção de recusar o onboarding (resposta negativa a \"Quer começar?\"):\n"
        '   {"action": "onboarding_no"}\n\n'
        "5. Não foi possível identificar:\n"
        '   {"action": "unknown"}\n'
    )


class AnthropicClient:
    def __init__(self) -> None:
        self._client: AsyncAnthropic | None = None

    def _get_client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._client

    async def classify_or_answer(self, user_message: str) -> dict:
        """
        Classify the user message and return one of:
          {"action": "menu", "option": "1".."7"}
          {"action": "faq", "answer": "..."}
          {"action": "onboarding_yes"}
          {"action": "onboarding_no"}
          {"action": "unknown"}

        Always returns a dict — never raises. On API error falls back to "unknown".
        """
        if not settings.anthropic_api_key:
            return {"action": "unknown"}

        raw = ""
        try:
            response = await self._get_client().messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=[
                    {
                        "type": "text",
                        "text": _build_system_prompt(),
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text.strip()
            # Strip markdown code fences if the model wraps the JSON
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Claude retornou JSON inválido: {!r}", raw)
            return {"action": "unknown"}
        except Exception as exc:
            logger.warning("Anthropic API error: {}", exc)
            return {"action": "unknown"}

    async def explain_emission_error(self, stage: str, error_msg: str) -> str:
        """
        Returns a helpful WhatsApp message explaining an NFS-e emission error.
        Falls back to a static message if the API is unavailable.
        """
        _FALLBACK = {
            "login": (
                "❌ *Falha no login* — CNPJ ou senha incorretos.\n\n"
                "⚠️ O bot usa login direto com *CNPJ + Senha* no portal nfse.gov.br. "
                "Login pelo *Gov.br não é suportado*.\n\n"
                "Se você só acessa pelo Gov.br, crie uma senha direta em nfse.gov.br → *Primeiro acesso*.\n\n"
                "Para atualizar seus dados, envie *3*."
            ),
        }
        default_fallback = (
            "❌ Falha ao emitir a NFS-e. Tente novamente ou contate o suporte (opção *4*)."
        )

        if not settings.anthropic_api_key:
            return _FALLBACK.get(stage, default_fallback)

        prompt = (
            f"Ocorreu um erro durante a emissão de NFS-e do usuário.\n"
            f"Etapa: {stage}\n"
            f"Erro técnico: {error_msg}\n\n"
            f"Escreva uma mensagem curta para o usuário no WhatsApp (máximo 5 linhas) explicando o problema "
            f"e o que ele deve fazer. Use *negrito* para destacar termos importantes. "
            f"Se o erro for de login, lembre que o bot NÃO suporta Gov.br — o usuário precisa de "
            f"senha direta no portal nfse.gov.br e pode atualizá-la pela opção 3."
        )

        raw = ""
        try:
            response = await self._get_client().messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system=[
                    {
                        "type": "text",
                        "text": _build_system_prompt(),
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            # Se Claude devolver JSON por engano, extraia o answer ou use fallback
            if raw.startswith("{"):
                parsed = json.loads(raw)
                return parsed.get("answer", _FALLBACK.get(stage, default_fallback))
            return raw
        except Exception as exc:
            logger.warning("Anthropic API error em explain_emission_error: {}", exc)
            return _FALLBACK.get(stage, default_fallback)


anthropic_client = AnthropicClient()
