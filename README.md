# 🤖 invest-agent

> **The LLM proposes, the code disposes.**

[![tests](https://github.com/leoheim/invest-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/leoheim/invest-agent/actions/workflows/tests.yml)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![tests](https://img.shields.io/badge/tests-277%20✓-brightgreen)
![status](https://img.shields.io/badge/phase-paper%20trading%20(testnet)-orange)

A personal crypto investing agent for Binance spot. Claude models read the
market and the news and produce **typed proposals**; a **deterministic rules
engine** — pure Python, 100% tested, no LLM — decides whether a proposal
becomes an order. The LLM never sees an API key, never sizes a position, and
only ever sees account state read-only.

```
 news ──────┐                                  ┌──────────────┐
 candles ───┤   ┌─────────┐    Proposal   ┌────┴───────┐      │
 macro ─────┼──▶│ Claude  │─────────────▶│ RulesEngine │──▶ Verdict
 positions ─┘   │proposes │ {asset, side, │   (code     │      │
                └─────────┘  conviction}  │  disposes)  │  APPROVED → order
                                          └────┬────────┘  REJECTED → reasons
                                               │            NEEDS_APPROVAL → Telegram
                         whitelist · sizing · exposure · frequency
                         market quality · circuit breakers · kill switch
```

## ⚠️ Disclaimer

This is a **personal project for educational purposes**. It is not financial
advice, and past backtest results guarantee nothing. Trading cryptocurrency
carries substantial risk — **never risk money you cannot afford to lose**.
The agent is designed to run for months on the Binance **testnet** (paper
trading) before ever touching real funds, and even then starts with a
micro-allocation. Use at your own risk; the author assumes no responsibility
for your trading results. **Always start with `--dry-run`.**

## ✨ Features

- 🧮 **Deterministic rules engine** — whitelist, sizing, exposure caps,
  frequency limits, market-quality gates, drawdown circuit breakers and a
  file-based kill switch. Pure Python, zero network, zero LLM. A proposal
  only becomes an order if **every** gate passes.
- 🛡️ **Hard-coded risk profile** — position/exposure ceilings, stop-loss on
  the exchange at entry, human approval above a threshold. Only a human
  edits [`config.py`](src/invest_agent/config.py).
- 🧠 **Claude brain, strictly sandboxed** — Haiku triages the whitelist
  (≤3 candidates), Sonnet writes one typed proposal via structured outputs,
  Opus reviews the week through the Batch API into versioned `learnings/`.
  Any LLM failure (refusal, bad JSON, network) degrades to HOLD — the cycle
  never crashes because of the model.
- 🔒 **Fail-closed everywhere** — unknown order state is never retried
  blindly; a tripped breaker halts new orders; stale candles reject the
  cycle; approved-but-failed orders are never resubmitted automatically;
  the kill switch gates even human-approved orders.
- 📲 **Telegram as the control room** — push notifications for every cycle,
  material news on held assets, inline Approve/Reject buttons with a TTL,
  `/status`, `/pausar`, `/kill` and friends. Single authorized chat; the
  bot token can never leak through an error message.
- 👤 **Human-in-the-loop by design** — orders above 2% of capital, the first
  trade in a new asset and the first order after a circuit breaker all
  require explicit human approval, keyed on *executed* history.
- 📊 **Honest backtesting** — parameter sweep in pure Python, realistic
  fills in backtrader (next-candle open, fees + slippage), always compared
  against buy-and-hold under the same costs, with `--split` for
  out-of-sample validation. No LLM in backtests (old windows were in the
  model's training data — "winning" there is look-ahead, not edge).
- 🪶 **Small on purpose** — 5 runtime dependencies, no agent framework.
  Every I/O boundary (HTTP, clock, env, LLM, exchange) is injectable, which
  is why all 277 tests run in ~4s with **zero network access**.

**Stack:** Python 3.12 · [backtrader](https://www.backtrader.com/) ·
[DuckDB](https://duckdb.org/) + Parquet · SQLite ·
[Claude API](https://docs.claude.com) (Haiku 4.5 triage, Sonnet proposal,
Opus weekly review via Batch) · Telegram Bot API over stdlib.

- 📄 **Design spec:** [`docs/superpowers/specs/2026-09-05-invest-agent-design.md`](docs/superpowers/specs/2026-09-05-invest-agent-design.md)
- 🧭 **Operations runbook:** [`docs/ops.md`](docs/ops.md)

## 🗺️ Delivery phases

| Phase | Scope | Status |
|:---:|---|:---:|
| 0 | Rules engine + tests; zero LLM, zero network | ✅ done |
| 1 | Data ingestion + honest backtest vs buy-and-hold | ✅ code complete¹ |
| 2 | Paper trading (Binance testnet) + Telegram + Claude loop | ✅ code complete² |
| 3 | Live micro-allocation (R$ 1,000) | ⬜ |
| 4 | Gradual scale-up; US options module (paper first) | ⬜ |

¹ Phase 1 code is done and fully tested; the phase *gate* (run the backtest
on real data and compare against buy-and-hold) still needs a networked
machine: candle `ingest` → `backtest.run`.

² Phase 2 code is done and fully tested; the operational gates (1–3 months
of paper trading on the testnet, 30 days without an unhandled incident)
must be met before Phase 3 — see [`docs/ops.md`](docs/ops.md).

## 🛡️ Active risk profile: moderate

| Rule | Value |
|---|---|
| Max per asset | 10% of capital |
| Max total exposure | 60% invested |
| Stop-loss | 5% on every buy (STOP_LOSS_LIMIT on the exchange) |
| Frequency | ≤ 4 orders/day · 4h cooldown per asset |
| Circuit breakers | halt at −5% day · −10% week · −15% month |
| Human approval (HITL) | order > 2% of capital → Telegram |
| Kill switch | out-of-process file + dead-man switch |

Values live in [`src/invest_agent/config.py`](src/invest_agent/config.py) —
**only a human edits them.**

## 🧱 Project layout

```
src/invest_agent/
│                        # ── Phase 0: rules engine (pure Python, zero LLM, zero network)
├── models.py            # Proposal, Verdict, OrderIntent, Position, MarketSnapshot
├── config.py            # RiskProfile (moderate profile — values from the spec)
├── whitelist.py         # dynamic whitelist: top-20 USDT pairs by volume, no stablecoins
├── sizing.py            # sizing in code: conviction × per-asset/total ceilings
├── gates.py             # market quality + anti-overtrading
├── breakers.py          # drawdown circuit breakers (day/week/month)
├── killswitch.py        # file-based kill switch + dead-man switch (fail-closed)
├── engine.py            # RulesEngine: a proposal becomes an order only if ALL gates pass
│                        # ── Phase 1: data & validation
├── data/                # Binance candles → monthly Parquet + DuckDB; whitelist stats
├── indicators.py        # RSI/SMA in pure Python
├── backtest/            # sweep + backtrader with realistic costs vs buy-and-hold; --split
├── news/                # RSS + Google News → keyword triage → URL/SimHash dedupe
├── macro/               # Fear & Greed, Selic rate, FX (Brazilian Central Bank SGS)
├── storage/             # SQLite: append-only decision log, positions, halts, HITL, costs
│                        # ── Phase 2: testnet operation
├── settings.py          # everything via env; secrets never in logs (repr=False)
├── execution/           # Binance adapter: HMAC, LIMIT IOC, stop on exchange, no blind POST retry
├── orchestrator/        # hourly cycle: mark-to-market portfolio, snapshot, hard HITL
├── brain/               # Claude: Haiku triage → Sonnet proposal (structured outputs,
│                        #   refusal = HOLD) → news enrichment → Opus weekly review
├── telegram/            # stdlib bot: push, /status, button approvals with TTL
└── jobs/                # weekly persisted whitelist
```

46 test files mirror this tree — **no test touches the network, the clock or
the environment**: HTTP transport, clock and keys are always injectable.

## 🧪 Running the tests

```bash
python3 -m pip install -e . pytest
python3 -m pytest
```

277 tests in ~4s with zero external calls — the same suite runs in
[CI](.github/workflows/tests.yml) on every push.

## 📥 Candle ingestion (Phase 1)

```bash
python3 -m invest_agent.data.ingest --symbol BTCUSDT --interval 1h --since 2024-01-01
```

Historical backfill via [data.binance.vision](https://data.binance.vision)
(free) + a recent tail over public REST. Idempotent: running again only
downloads what's missing. Data lands in `data/candles/` (git-ignored) as
monthly Parquet partitions, queryable with DuckDB.

## 📊 Backtest vs buy-and-hold (Phase 1)

```bash
python3 -m invest_agent.backtest.run --symbol BTCUSDT --interval 1h --strategy sma_cross
# Out-of-sample validation: train on the first 70%, test on the rest
python3 -m invest_agent.backtest.run --symbol BTCUSDT --interval 1h --strategy sma_cross --split 0.7
```

Parameter sweep in pure Python → realistic fills in backtrader (orders
execute at the next candle's open, 0.10% commission + 0.05% slippage per
side) → a report comparing against buy-and-hold under identical costs.
Mechanical strategies only — no LLM in backtests (an old window was already
in the model's training data; an LLM "winning" there is look-ahead, not
edge). Use `--split` for honest validation.

## 🔁 Agent cycle (Phase 2 — testnet)

```bash
export BINANCE_API_KEY=... BINANCE_API_SECRET=...   # TESTNET keys
python3 -m invest_agent.orchestrator.cycle --dry-run
```

One full cycle: heartbeat → halt/API-cost gates → mark-to-market portfolio
reconciled from the exchange → proposal (HOLD proposer without the LLM) →
rules engine → append-only decision log → LIMIT IOC order + stop-loss on the
exchange. Without `--dry-run`, approved orders are sent to the testnet.

With `--llm` (and `ANTHROPIC_API_KEY` set), the cycle runs the full brain —
news enrichment, Haiku triage and a Sonnet proposal — before the rules
engine; without the flag, the proposer is HOLD (operational dry-run, the
default).

With `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` set, the cycle pushes
notifications to the owner: every cycle result, pending-approval orders (HITL)
with inline Approve/Reject buttons, and — with `--llm` — material news
(materiality ≥ 4) about held assets. An order approved in the bot does not
execute immediately: it is marked `approved` in the store and submitted at
the start of the next cycle, before any new proposal is evaluated.

## 📰 News & macro ingestion (Phase 1)

```bash
python3 -m invest_agent.news.ingest --macro
```

RSS (InfoMoney, Valor, MoneyTimes, CoinDesk, CoinTelegraph) + per-asset
Google News RSS (pt-BR, 1-day window) → keyword triage (only items citing
whitelisted assets or macro themes) → two-stage dedupe (canonical URL,
title SimHash) → SQLite (`data/agent.db`) with `published_at` ≠
`ingested_at` (anti look-ahead). `--macro` adds Fear & Greed, the Selic
rate and FX from the Brazilian Central Bank (SGS).

## 🧭 Operations

The full operations runbook (VPS, credentials, cron, the bot's systemd
unit, dead-man switch, Telegram commands, phase-transition gates and the
testnet → live checklist) lives in [`docs/ops.md`](docs/ops.md).

> **Note:** the agent speaks **Portuguese** to its owner — Telegram
> messages, bot replies and decision-log reasons are pt-BR by design.

## ⚠️ Known limitations

### Phase 0

- **Mark-to-market exposure:** the 60% invested ceiling inside the sizing
  rule values existing positions at avg_price (cost), not market price.
  When positions have appreciated, this UNDERSTATES total exposure and may
  approve a buy that silently pushes real (mark-to-market) exposure past
  the ceiling. The error is one-directional and permissive on exposure;
  the orchestrator's mark-to-market equity (Phase 2) narrows the impact.
- **Gates on exits:** SELL/CLOSE orders still pass through the frequency,
  market-quality, circuit-breaker and kill-switch gates — intentionally
  fail-closed; a de-risking exit can be delayed by a cooldown or a wide
  spread.

### Phase 1

- **In-sample selection:** the sweep picks the best parameters in the same
  window the verdict is computed on (optimistic by construction). Treat
  the default report as screening, not validation; use `--split 0.7` for
  honest out-of-sample validation.

### Phase 2

- **Exchange filters not applied:** the execution adapter does not read
  the per-symbol `LOT_SIZE`/`PRICE_FILTER`/`NOTIONAL` filters from
  `exchange_info` — an order can be rejected by the exchange for invalid
  quantity/price precision. Checklist and manual workaround in
  [`docs/ops.md`](docs/ops.md#8-phase-gates-spec-5); automatic application
  is a planned improvement. The complete technical-debt list
  (reconcile-on-boot, tool-error-rate breaker, LLM client timeout, etc.)
  is documented in
  [`docs/ops.md`](docs/ops.md#10-inherited-technical-debt-known-non-blocking).
