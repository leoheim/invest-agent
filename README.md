# 🤖 invest-agent

> **O LLM propõe, código dispõe.**

Agente pessoal de investimento em cripto (Binance spot). Modelos Claude analisam
mercado e notícias e produzem **propostas tipadas**; um **motor de regras
determinístico** — Python puro, 100% testado, sem LLM — decide se a proposta
vira ordem. O LLM nunca vê chave de API, nunca calcula tamanho de posição e
enxerga o estado da conta apenas em modo leitura.

```
 notícias ─┐                                   ┌──────────────┐
 candles ──┤   ┌─────────┐    Proposal    ┌────┴───────┐      │
 macro ────┼──▶│ Claude  │──────────────▶│ RulesEngine │──▶ Verdict
 posições ─┘   │ propõe  │  {ativo, ação, │  (código    │      │
               └─────────┘   convicção}   │   dispõe)   │  APPROVED → ordem
                                          └────┬────────┘  REJECTED → motivos
                                               │            NEEDS_APPROVAL → Telegram
                        whitelist · sizing · exposição · frequência
                        qualidade de mercado · circuit breakers · kill switch
```

- 📄 **Spec:** [`docs/superpowers/specs/2026-09-05-invest-agent-design.md`](docs/superpowers/specs/2026-09-05-invest-agent-design.md)
- 🔬 **Pesquisa de fundamentação:** `~/Documents/research/` (6 relatórios, 2026-09-05)

## 🗺️ Fases de entrega

| Fase | Conteúdo | Status |
|:---:|---|:---:|
| 0 | Motor de regras + testes; zero LLM, zero rede | ✅ concluída |
| 1 | Ingestão de dados + backtest honesto vs buy-and-hold | 🚧 em andamento |
| 2 | Paper trading (testnet Binance) + Telegram + loop Claude | ⬜ |
| 3 | Live micro com R$ 1.000 | ⬜ |
| 4 | Escala gradual; módulo de opções EUA (paper primeiro) | ⬜ |

## 🛡️ Perfil de risco ativo: moderado

| Regra | Valor |
|---|---|
| Máximo por ativo | 10% do capital |
| Exposição total máxima | 60% investido |
| Stop-loss | 5% em toda compra (OCO na exchange) |
| Frequência | ≤ 4 ordens/dia · cooldown 4h por ativo |
| Circuit breakers | halt a −5% dia · −10% semana · −15% mês |
| Aprovação humana (HITL) | ordem > 2% do capital → Telegram |
| Kill switch | arquivo fora do processo + dead-man switch |

Valores em [`src/invest_agent/config.py`](src/invest_agent/config.py) — **só um humano edita.**

## 🧱 Estrutura

```
src/invest_agent/
├── models.py      # Proposal, Verdict, OrderIntent, Position, MarketSnapshot
├── config.py      # RiskProfile (perfil moderado — valores da spec)
├── whitelist.py   # whitelist dinâmica: top-20 USDT por volume, sem stablecoins
├── sizing.py      # sizing por código: convicção × tetos por ativo/total
├── gates.py       # qualidade de mercado + anti-overtrading
├── breakers.py    # circuit breakers de drawdown (dia/semana/mês)
├── killswitch.py  # kill switch em arquivo + dead-man switch (falha fechado)
└── engine.py      # RulesEngine: compõe tudo; proposta só vira ordem se TODOS passarem
```

## 🧪 Rodar os testes

```bash
python3 -m pip install pytest
python3 -m pytest -v
```

## ⚠️ Limitações conhecidas (Fase 0)

- **Exposição mark-to-market:** o teto de 60% investido na regra de sizing
  avalia demais posições a avg_price (custo), não a preço de mercado.
  Quando posições se valorizaram desde a entrada, isto SUBESTIMA o total
  investido, podendo aprovar uma compra que faz a exposição real
  (mark-to-market) ultrapassar silenciosamente o teto. O erro é
  unidirecional e permissivo em exposição. Em produção (Fase 1+), o
  orquestrador com market data completo recalculará antes de enviar à
  exchange.
- **HITL de primeira entrada:** aprovação humana para primeiro trade em
  ativo novo adiada para Fase 2 (o gatilho de 2% já cobre entradas com
  conviction > 0.2).
- **HITL pós-circuit-breaker:** aprovação humana para reentrada após
  circuit breaker adiada para Fase 2.
- **Gates em saídas:** saídas (SELL/CLOSE) ainda passam pelos gates de
  frequência, qualidade de mercado, circuit breakers e kill switch —
  comportamento fail-closed intencional na Fase 0; uma saída de
  de-risking pode ser atrasada por cooldown ou spread alto; revisitar
  na Fase 1.
