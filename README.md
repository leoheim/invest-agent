# 🤖 invest-agent

> **O LLM propõe, código dispõe.**

[![tests](https://github.com/leoheim/invest-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/leoheim/invest-agent/actions/workflows/tests.yml)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![testes](https://img.shields.io/badge/testes-277%20✓-brightgreen)
![status](https://img.shields.io/badge/fase-paper%20trading%20(testnet)-orange)

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

- 📄 **Spec de design:** [`docs/superpowers/specs/2026-09-05-invest-agent-design.md`](docs/superpowers/specs/2026-09-05-invest-agent-design.md)
- 🧭 **Runbook de operação:** [`docs/ops.md`](docs/ops.md)

**Stack:** Python 3.12 · [backtrader](https://www.backtrader.com/) · [DuckDB](https://duckdb.org/) + Parquet · SQLite · [API da Claude](https://docs.claude.com) (Haiku 4.5 na triagem, Sonnet na proposta, Opus na revisão semanal via Batch) · Telegram Bot API em stdlib — 4 dependências de runtime, nenhum framework de agente.

| | |
|---|---|
| 🛡️ [Perfil de risco](#%EF%B8%8F-perfil-de-risco-ativo-moderado) | tetos, breakers, kill switch — hardcoded, só humano edita |
| 🧱 [Estrutura](#-estrutura) | motor de regras puro + camadas de dados/execução/cérebro |
| 🧪 [Testes](#-rodar-os-testes) | 277 testes, zero rede/relógio/env — tudo injetável |
| 🔁 [Ciclo do agente](#-ciclo-do-agente-fase-2--testnet) | dry-run → testnet → `--llm` com cérebro completo |
| 🧭 [Operação](#-operação) | VPS, cron, systemd, dead-man switch, gates das fases |

## 🗺️ Fases de entrega

| Fase | Conteúdo | Status |
|:---:|---|:---:|
| 0 | Motor de regras + testes; zero LLM, zero rede | ✅ concluída |
| 1 | Ingestão de dados + backtest honesto vs buy-and-hold | ✅ código concluído¹ |
| 2 | Paper trading (testnet Binance) + Telegram + loop Claude | ✅ código concluído² |
| 3 | Live micro com R$ 1.000 | ⬜ |
| 4 | Escala gradual; módulo de opções EUA (paper primeiro) | ⬜ |

¹ Código da Fase 1 pronto e 100% testado; o *gate* da fase (rodar o backtest
com dados reais e comparar com buy-and-hold) ainda precisa ser executado numa
máquina com rede: `ingest` de candles → `backtest.run`.

² Código da Fase 2 pronto e 100% testado; gates operacionais (1-3 meses de
paper trading na testnet, 30 dias sem incidente não tratado) ainda por
cumprir antes da Fase 3 — ver [`docs/ops.md`](docs/ops.md).

## 🛡️ Perfil de risco ativo: moderado

| Regra | Valor |
|---|---|
| Máximo por ativo | 10% do capital |
| Exposição total máxima | 60% investido |
| Stop-loss | 5% em toda compra (STOP_LOSS_LIMIT na exchange) |
| Frequência | ≤ 4 ordens/dia · cooldown 4h por ativo |
| Circuit breakers | halt a −5% dia · −10% semana · −15% mês |
| Aprovação humana (HITL) | ordem > 2% do capital → Telegram |
| Kill switch | arquivo fora do processo + dead-man switch |

Valores em [`src/invest_agent/config.py`](src/invest_agent/config.py) — **só um humano edita.**

## 🧱 Estrutura

```
src/invest_agent/
│                        # ── Fase 0: motor de regras (Python puro, zero LLM, zero rede)
├── models.py            # Proposal, Verdict, OrderIntent, Position, MarketSnapshot
├── config.py            # RiskProfile (perfil moderado — valores da spec)
├── whitelist.py         # whitelist dinâmica: top-20 USDT por volume, sem stablecoins
├── sizing.py            # sizing por código: convicção × tetos por ativo/total
├── gates.py             # qualidade de mercado + anti-overtrading
├── breakers.py          # circuit breakers de drawdown (dia/semana/mês)
├── killswitch.py        # kill switch em arquivo + dead-man switch (falha fechado)
├── engine.py            # RulesEngine: proposta só vira ordem se TODOS os gates passarem
│                        # ── Fase 1: dados e validação
├── data/                # candles Binance → Parquet mensal + DuckDB; stats p/ whitelist
├── indicators.py        # RSI/SMA em Python puro
├── backtest/            # sweep + backtrader com custos realistas vs buy-and-hold; --split
├── news/                # RSS + Google News → triagem por keyword → dedupe URL/SimHash
├── macro/               # Fear & Greed, Selic, câmbio (BCB SGS)
├── storage/             # SQLite: decision log append-only, posições, halt, HITL, custos
│                        # ── Fase 2: operação na testnet
├── settings.py          # tudo por env; segredos nunca em logs (repr=False)
├── execution/           # adapter Binance: HMAC, LIMIT IOC, stop na exchange, sem retry de POST
├── orchestrator/        # ciclo horário: portfólio mark-to-market, snapshot, HITL duro
├── brain/               # Claude: triagem Haiku → proposta Sonnet (structured outputs,
│                        #   refusal = HOLD) → enriquecimento de notícias → revisão semanal Opus
├── telegram/            # bot stdlib: push, /status, aprovação por botões com TTL
└── jobs/                # whitelist semanal persistida
```

46 arquivos de teste espelham essa árvore — **nenhum teste toca rede, relógio ou env**: transporte HTTP, clock e chaves são sempre injetáveis.

## 🧪 Rodar os testes

```bash
python3 -m pip install -e . pytest
python3 -m pytest
```

277 testes em ~4s, sem nenhuma chamada externa — a mesma suíte roda no
[CI](.github/workflows/tests.yml) a cada push.

## 📥 Ingestão de candles (Fase 1)

```bash
python3 -m invest_agent.data.ingest --symbol BTCUSDT --interval 1h --since 2024-01-01
```

Backfill histórico via [data.binance.vision](https://data.binance.vision)
(grátis) + cauda recente via REST público. Idempotente: rodar de novo só
baixa o que falta. Os dados ficam em `data/candles/` (fora do git),
particionados em Parquet mensal e consultáveis com DuckDB.

## 📊 Backtest vs buy-and-hold (Fase 1)

```bash
python3 -m invest_agent.backtest.run --symbol BTCUSDT --interval 1h --strategy sma_cross
# Validação out-of-sample: treina no primeiro 70%, testa no restante
python3 -m invest_agent.backtest.run --symbol BTCUSDT --interval 1h --strategy sma_cross --split 0.7
```

Sweep de parâmetros em Python puro → fills realistas no backtrader (ordem
executa na abertura do candle seguinte, comissão 0,10% + slippage 0,05%
por lado) → relatório comparando com buy-and-hold sob os mesmos custos.
Estratégias mecânicas apenas — sem LLM em backtest (janela antiga já
esteve no treino do modelo; um LLM "acertando" ali é look-ahead, não edge).
Use `--split` para validação honesta (treina no período inicial e testa no restante).

## 🔁 Ciclo do agente (Fase 2 — testnet)

```bash
export BINANCE_API_KEY=... BINANCE_API_SECRET=...   # chaves da TESTNET
python3 -m invest_agent.orchestrator.cycle --dry-run
```

Um ciclo completo: heartbeat → halt/custo de API → carteira mark-to-market
reconciliada da exchange → proposta (sem LLM por enquanto: proposer HOLD) →
motor de regras → decision log append-only → ordem LIMIT IOC + stop-loss na
exchange. Sem `--dry-run`, ordens aprovadas são enviadas à testnet.

Com `--llm` (e `ANTHROPIC_API_KEY` configurada), o ciclo roda o cérebro
completo — enriquecimento de notícias, triagem Haiku e proposta Sonnet —
antes do motor de regras; sem a flag, o proposer é HOLD (dry-run operacional,
comportamento padrão).

Com `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` configurados, o ciclo empurra
notificações para o dono via Telegram: resultado de cada ciclo, ordens de
aprovação pendente (HITL) com botões inline "Aprovar"/"Rejeitar", e — com
`--llm` — notícias materiais (materialidade ≥ 4) sobre ativos em carteira.
Uma ordem aprovada pelo dono no bot não executa na hora: ela fica marcada
`approved` no store e é enviada no ciclo seguinte, antes de qualquer nova
proposta ser avaliada.

## 📰 Ingestão de notícias e macro (Fase 1)

```bash
python3 -m invest_agent.news.ingest --macro
```

RSS (InfoMoney, Valor, MoneyTimes, CoinDesk, CoinTelegraph) + Google News
RSS por ativo da whitelist (pt-BR, janela de 1 dia) → triagem por
keyword (só o que cita ativos da whitelist ou temas macro) → dedupe em dois
estágios (URL canônica, SimHash de título) → SQLite (`data/agent.db`) com
`published_at` ≠ `ingested_at` (anti look-ahead). `--macro` adiciona Fear &
Greed, Selic e câmbio (BCB SGS). Dedupe por embedding e enriquecimento LLM
ficam para a Fase 2.

## 🧭 Operação

Runbook completo de operação (VPS, credenciais, cron, systemd do bot,
dead-man switch, comandos do Telegram, gates de transição entre fases e
checklist testnet → live) em [`docs/ops.md`](docs/ops.md).

## ⚠️ Limitações conhecidas

### (Fase 0)

- **Exposição mark-to-market:** o teto de 60% investido na regra de sizing
  avalia demais posições a avg_price (custo), não a preço de mercado.
  Quando posições se valorizaram desde a entrada, isto SUBESTIMA o total
  investido, podendo aprovar uma compra que faz a exposição real
  (mark-to-market) ultrapassar silenciosamente o teto. O erro é
  unidirecional e permissivo em exposição. Em produção (Fase 1+), o
  orquestrador com market data completo recalculará antes de enviar à
  exchange.
- **Gates em saídas:** saídas (SELL/CLOSE) ainda passam pelos gates de
  frequência, qualidade de mercado, circuit breakers e kill switch —
  comportamento fail-closed intencional na Fase 0; uma saída de
  de-risking pode ser atrasada por cooldown ou spread alto; revisitar
  na Fase 1.

### (Fase 1)

- **Seleção in-sample:** o sweep escolhe os melhores parâmetros na mesma
  janela em que o veredito é calculado (otimismo por construção).
  Trate o resultado como triagem, não como validação out-of-sample;
  use `--split 0.7` para validação honesta.

### (Fase 2)

- **Exchange filters não aplicados:** o adapter de execução não conhece
  os filtros `LOT_SIZE`/`PRICE_FILTER`/`NOTIONAL` de `exchange_info` por
  símbolo — uma ordem pode ser rejeitada pela exchange por precisão
  inválida de quantidade/preço. Checklist e contorno manual em
  [`docs/ops.md`](docs/ops.md#8-gates-das-fases-spec-5); aplicação
  automática é melhoria futura. Débito técnico completo (reconcile-on-boot,
  breaker de erro de tools, timeout do LLM etc.) documentado em
  [`docs/ops.md`](docs/ops.md#10-débito-técnico-herdado-itens-conhecidos-não-bloqueantes-desta-fase).
