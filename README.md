# invest-agent

Agente de investimento pessoal: **o LLM propõe, código dispõe.**

Modelos Claude analisam mercado e notícias e produzem *propostas*
(`Proposal`); um motor de regras determinístico (`RulesEngine`) valida
cada proposta contra whitelist, sizing, exposição, frequência, circuit
breakers e kill switch antes de qualquer ordem existir. O LLM nunca vê
chave de API e nunca calcula tamanho de posição.

- Spec: `docs/superpowers/specs/2026-09-05-invest-agent-design.md`
- Pesquisa que fundamenta o design: `~/Documents/research/` (6 relatórios)

## Estado atual

**Fase 0 concluída:** modelos de domínio + motor de regras completo,
100% testado, zero rede/LLM. Próximas fases (spec §5): ingestão +
backtest → paper trading (testnet Binance + Telegram + loop Claude) →
live micro com R$ 1.000.

## Rodar os testes

    python3 -m pip install pytest
    python3 -m pytest -v

## Perfil de risco ativo: moderado

Máx 10% por ativo · máx 60% investido · stop-loss 5% em toda compra ·
≤4 ordens/dia · cooldown 4h por ativo · halt a −5% dia / −10% semana /
−15% mês · aprovação humana via Telegram acima de 2% do capital.
Valores em `src/invest_agent/config.py` — só um humano edita.

## Limitações conhecidas (Fase 0)

- **Exposição mark-to-market:** o teto de 60% investido na regra de sizing
  avalia demais posições a avg_price (custo), não a preço de mercado. Em
  produção (Fase 1+), o orquestrador com market data completo recalculará
  antes de enviar à exchange.
- **HITL de primeira entrada:** aprovação humana para primeiro trade em
  ativo novo adiada para Fase 2 (o gatilho de 2% já cobre entradas com
  conviction > 0.2).
- **HITL pós-circuit-breaker:** aprovação humana para reentrada após
  circuit breaker adiada para Fase 2.
