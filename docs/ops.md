# 🛠️ Operations Runbook — invest-agent

Operational guide for running the agent on a VPS: provisioning, credentials,
cron, systemd, dead-man switch, day-to-day commands, and the gates that
define when to move on to the next phase. Complements the spec
([`docs/superpowers/specs/2026-09-05-invest-agent-design.md`](superpowers/specs/2026-09-05-invest-agent-design.md))
— this document is about **how to operate**, not about **how the engine decides**.

## 1. Prerequisites

- Linux VPS (e.g. Hetzner CX22 — 2 vCPU/4GB is enough; there is no local
  model training).
- Static IP — required for the Binance IP whitelist once you switch to the
  real key.
- Python ≥ 3.12.
- Dependencies: `pip install duckdb pyarrow backtrader anthropic pytz` (the
  rest of the runtime is stdlib — spec §4.5, zero unnecessary
  dependencies).
- Clone of the repository at `/opt/invest-agent` (path used in the
  cron/systemd examples below; adjust if you use a different one).

```bash
git clone <repo> /opt/invest-agent
cd /opt/invest-agent
python3 -m venv .venv && source .venv/bin/activate
pip install duckdb pyarrow backtrader anthropic pytz pytest
mkdir -p data logs
```

## 2. Credentials (environment variables)

All variables are read by `Settings.from_env` (`src/invest_agent/settings.py`).
None of them has a working example value — fill in your own.
Store them in `/etc/invest-agent.env` (permission `600`, owned by the
service user) — never in the repository, never in logs (spec §4.5: secrets
live in env vars, never in repo/prompt/log; `Settings` uses `repr=False` on
secret fields so they don't leak in a traceback).

| Variable | Required | Where to obtain it | Notes |
|---|:---:|---|---|
| `BINANCE_API_KEY` | yes | testnet: [testnet.binance.vision](https://testnet.binance.vision) (log in via GitHub) → generates a test key pair. Production: binance.com → API Management. | Real key: **no withdrawal permission**, with IP whitelist. See the transition checklist (§8). |
| `BINANCE_API_SECRET` | yes | same screen as the API key. | Never commit to version control; never log it. |
| `BINANCE_BASE_URL` | no (default `https://testnet.binance.vision`) | — | Switch to `https://api.binance.com` only during the transition to live (§8) and to run the whitelist job (§3 / note on T7 — public production reads even during the paper phase). |
| `TELEGRAM_BOT_TOKEN` | yes | [@BotFather](https://t.me/BotFather) → `/newbot`. | Bot token; never appears in logs (the stdlib HTTP client guarantees this — spec §4.6). |
| `TELEGRAM_CHAT_ID` | yes | [@userinfobot](https://t.me/userinfobot) → send `/start`, it replies with the `id`. | A single authorized chat — updates from any other `chat_id` are discarded by the client. |
| `ANTHROPIC_API_KEY` | yes (for `--llm`) | [console.anthropic.com](https://console.anthropic.com) → API Keys. | Without it, the cycle runs in deterministic HOLD mode (no brain). |
| `API_COST_DAILY_CAP_USD` | no (default `2.0`) | choose your own | Daily spending cap for the LLM API — above this, the day's cycle stops calling the brain. |
| `INVEST_DB_PATH` | no (default `data/agent.db`) | — | Path to the SQLite file (decision log, positions, halt, whitelist, pending approvals). |
| `INVEST_CANDLES_ROOT` | no (default `data/candles`) | — | Root of the partitioned Parquet candle store. |
| `INVEST_KILL_PATH` | no (default `data/KILL`) | — | Kill switch file — its existence means the agent is stopped. |
| `INVEST_HEARTBEAT_PATH` | no (default `data/heartbeat`) | — | Timestamp of the last live cycle — consumed by the dead-man switch (§6). |

**Signature migration (noted, not implemented):** the execution adapter
(`execution/binance_adapter.py`) signs requests with HMAC (symmetric
`BINANCE_API_SECRET`). Binance recommends **Ed25519** for new keys —
private-key signing, with no shared secret in transit. When generating the
real production key, prefer Ed25519 and treat the HMAC→Ed25519 adapter swap
as a future improvement item (§9); until then, HMAC with a
withdrawal-disabled key is acceptable.

## 3. Bootstrap (first time)

```bash
# 1) candle history for each whitelist symbol (adjust the list/--since)
for s in BTCUSDT ETHUSDT SOLUSDT; do
  python3 -m invest_agent.data.ingest --symbol $s --interval 1h --since 2024-01-01
done

# 2) compute and persist the real whitelist (runs against production — read-only public data, see §4)
BINANCE_BASE_URL=https://api.binance.com python3 -m invest_agent.jobs.whitelist

# 3) initial news + macro load
python3 -m invest_agent.news.ingest --macro

# 4) first cycle in safe mode — sends no orders at all
python3 -m invest_agent.orchestrator.cycle --dry-run
```

Only after checking the `--dry-run` output (proposal, verdict, reasons)
should the cron (§4) be installed.

## 4. Cron

Before enabling the cron, configure the search path and the environment
variables. Add this to the top of the crontab (`crontab -e`):

```bash
PATH=/opt/invest-agent/.venv/bin:/usr/bin:/bin
```

Create the loader wrapper (`/opt/invest-agent/bin/with-env`, `chmod +x`):

```bash
#!/bin/sh
set -a
. /etc/invest-agent.env
set +a
exec "$@"
```

Every cron command below must be prefixed with this wrapper. Example
(adapted from the news line in the block below):

```bash
*/15 * * * * cd /opt/invest-agent && /opt/invest-agent/bin/with-env python3 -m invest_agent.news.ingest >> logs/news.log 2>&1
```

Without this: `--llm` runs with no brain (ANTHROPIC_API_KEY empty), the
whitelist queries the testnet (default BINANCE_BASE_URL), and the dead-man
switch can't alert via Telegram.

The commands below — copy them into the crontab with the wrapper prefixed
to each line:

```cron
# ORDER MATTERS: candles at :01 (the hourly candle just closed), cycle at :05 —
# the quality gate rejects a candle older than 600s (spec); outside that window the cycle fails on stale data.
1 * * * *   cd /opt/invest-agent && for s in $(python3 -c "from invest_agent.storage.sqlite_store import SqliteStore; s=SqliteStore('data/agent.db'); print(' '.join(sorted(s.get_whitelist()))); s.close()"); do python3 -m invest_agent.data.ingest --symbol $s --interval 1h; done >> logs/candles.log 2>&1
5 * * * *   cd /opt/invest-agent && python3 -m invest_agent.orchestrator.cycle --llm >> logs/cycle.log 2>&1
*/15 * * * * cd /opt/invest-agent && python3 -m invest_agent.news.ingest >> logs/news.log 2>&1
10 6 * * *  cd /opt/invest-agent && python3 -m invest_agent.news.ingest --macro >> logs/macro.log 2>&1
0 5 * * 1   cd /opt/invest-agent && python3 -m invest_agent.jobs.whitelist >> logs/whitelist.log 2>&1
0 9 * * *   cd /opt/invest-agent && python3 -m invest_agent.telegram.bot --digest >> logs/digest.log 2>&1
0 22 * * 0  cd /opt/invest-agent && python3 -m invest_agent.brain.weekly --submit >> logs/weekly.log 2>&1
0 10 * * 1  cd /opt/invest-agent && python3 -m invest_agent.brain.weekly --collect $(cat data/last_batch_id 2>/dev/null) >> logs/weekly.log 2>&1
```

(note: `--submit` prints the batch id; redirecting it to
`data/last_batch_id` via a simple wrapper is the runbook's suggestion — for
example, replacing the Sunday 22:00 line with a one-line script that does
`python3 -m invest_agent.brain.weekly --submit | tee -a logs/weekly.log | tail -1 > data/last_batch_id`.)

The bot (`/status /perfil /pausar /retomar /kill /aprovar /rejeitar` +
buttons) **does not run under cron** — it runs continuously via systemd
(§5), long-polling the Bot API's `getUpdates`.

## 5. systemd (Telegram bot)

Create the system user and adjust permissions:

```bash
sudo useradd --system --create-home --home-dir /opt/invest-agent invest-agent
sudo chown -R invest-agent: /opt/invest-agent
```

Unit file `/etc/systemd/system/invest-agent-bot.service`:

```ini
[Unit]
Description=invest-agent — bot Telegram (comandos, botões, digest)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=invest-agent
WorkingDirectory=/opt/invest-agent
EnvironmentFile=/etc/invest-agent.env
ExecStart=/opt/invest-agent/.venv/bin/python3 -m invest_agent.telegram.bot
Restart=always
RestartSec=10
StandardOutput=append:/opt/invest-agent/logs/bot.log
StandardError=append:/opt/invest-agent/logs/bot.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now invest-agent-bot.service
sudo systemctl status invest-agent-bot.service
```

`Restart=always` covers process crashes; the kill switch and the halt live
in a file/SQLite outside the process, so a bot restart doesn't lose state
(spec — the kill switch is designed to survive the process being killed).

## 6. Dead-man switch

Cron every 10 minutes: if the cycle's heartbeat is older than 2h (2 missed
cycles, a margin over the hourly cron), it activates the kill switch and
alerts via Telegram. Inline script, no new dependency:

```cron
*/10 * * * * cd /opt/invest-agent && python3 -c "
from datetime import datetime, timezone
from invest_agent.settings import Settings
from invest_agent.killswitch import KillSwitch, heartbeat_stale
import os, json, urllib.request
s = Settings.from_env(os.environ)
if heartbeat_stale(s.heartbeat_path, 7200, datetime.now(timezone.utc)):
    KillSwitch(s.kill_switch_path).activate('dead-man switch: heartbeat parado')
    body = json.dumps({'chat_id': s.telegram_chat_id, 'text': '🛑 dead-man switch: heartbeat parado há mais de 2h — kill switch ativado'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{s.telegram_token}/sendMessage', data=body, headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(req, timeout=10)
" >> logs/deadman.log 2>&1
```

Fail-closed by design: with no heartbeat file, `heartbeat_stale` returns
`True` (see `killswitch.py`) — total silence also triggers the kill switch,
not just a stale heartbeat.

## 7. Operations

**Bot commands** (Telegram, single authorized chat):

| Command | Effect |
|---|---|
| `/status` | registered positions, active halt, today's API cost, pending approvals. |
| `/perfil` | parameters of the active risk profile (moderate). |
| `/pausar` | activates the kill switch (`"pausado via telegram"`) — no new orders until `/retomar`. |
| `/retomar` | deactivates the kill switch **and** releases the persisted halt. |
| `/kill` | activates the kill switch with reason `"kill via telegram"` — same effect as `/pausar`, emergency semantics. |
| `/aprovar <id>` / `/rejeitar <id>` | resolves a HITL pending item by id (also available as inline buttons `ap:<id>`/`rj:<id>` on the original message). Expired id → replies "expirada" (expired), does not execute. |

**HITL flow:** the cycle detects the need for approval (large order, first
trade in a new asset, or first order after a circuit breaker — spec §4.4)
and sends the proposal to Telegram with buttons. Approving/rejecting only
marks the status in the store — **the order itself is only sent on the
next scheduled cycle** (cron every 5 minutes on the hour, or the next
`:05`). To act immediately after approving (without waiting for the next
cron slot):

```bash
cd /opt/invest-agent && python3 -m invest_agent.orchestrator.cycle --llm
```

**MONTH-level halt:** this is the most severe of the circuit breakers
(monthly drawdown ≥ 15%, moderate profile). **Do not run `/retomar` as a
reflex** — manually review the `decision_log` (`read_decisions()` / direct
SQLite) and the halt reason before releasing it. DAY/WEEK-level halts are
expected as part of normal operation; MONTH is a signal that something
went off-script and deserves a real review before unlocking.

## 8. Phase gates (spec §5)

- **Phase 2 (current — paper trading):** 1 to 3 months running on the
  Binance testnet, with no manual intervention outside the normal HITL
  flow, **30 consecutive days with no unhandled incident** (kill switch
  triggered by a bug, data loss, incorrect order, etc. counts as an
  incident; a correctly handled circuit-breaker halt does not).
- **Phase 3 (live micro):** switch `BINANCE_BASE_URL` to production
  (`https://api.binance.com`) with a real key and R$ 1,000 in capital.
  Entry gate: Phase 2 completed + ≥ 1 month of reviewed paper-trading
  metrics (Opus weekly review, decision log) showing behavior within
  expectations.
- **Phase 4 (scale/options):** gradual capital scaling and the US options
  module (paper first) — only with Phase 3 evidence (real, not simulated,
  metrics) supporting the decision.

**Testnet → live transition checklist:**

1. Generate a production API key **with no withdrawal permission**.
2. Configure the VPS's IP whitelist on the key.
3. Update `BINANCE_API_KEY`/`BINANCE_API_SECRET`/`BINANCE_BASE_URL` in
   `/etc/invest-agent.env` (never in the repo).
4. **Check the symbol's `LOT_SIZE`/`NOTIONAL` filters** via
   `exchange_info` before trading — **current limitation:** the execution
   adapter does not apply Binance's tick-size/step-size exchange filters.
   An order may be rejected by the exchange for invalid quantity/price
   precision; if this happens, manually round `qty`/`price` according to
   the symbol's filter until the automatic fix is implemented (§9).
5. Run `--dry-run` once against production before re-enabling the real
   cron, just to confirm that market/portfolio reads are correct.
6. Restart the bot's systemd service with the new `EnvironmentFile`.

## 9. Noted future extensions

- **Free-form chat in the bot:** today only fixed commands exist; allowing
  free-form questions from the owner via a **read-only** LLM (no side
  effects) is left for later — out of scope for this phase by explicit
  decision.
- **OCO with take-profit:** today only the stop-loss is placed on the
  exchange alongside the entry; adding the take-profit leg (a full OCO
  order) is a future improvement.
- **Ed25519:** replace the execution adapter's HMAC signing with Ed25519
  (see the note in the credentials table, §2).
- **Automatic exchange filters:** automatically apply
  `LOT_SIZE`/`PRICE_FILTER`/`NOTIONAL` from `exchange_info` in the
  adapter, instead of manual adjustment (see checklist §8, item 4).
- **News dedupe by embedding:** today deduplication is only canonical URL
  + title SimHash; semantic dedupe by embedding is left for Phase 2+.
- **Module 2 (US options):** Phase 4 scope, not started.

## 10. Inherited technical debt (known, non-blocking)

Living list of real pending items left by earlier phases — the owner
should know them before operating live. None of them block paper-trading
operation; all deserve review before Phase 3.

- **Reconcile-on-boot (spec §4.5):** `get_order` exists to reconcile an
  order's state with the exchange, but nothing calls it at process
  startup. A crash between the order's `POST` and persisting the result
  can lose a fill (the agent would have no way of knowing the order was
  executed).
- **Swallowed cancel reason:** when canceling a stop fails, the error is
  silently swallowed — an observability gap; today there's no way to
  tell, just by looking at the log, that a stop failed to be canceled as
  it should have been.
- **Tool error-rate breaker (spec §4.4):** not implemented — there is
  currently no circuit breaker specific to the error rate of the LLM's
  tool calls.
- **Exposure cap uses avg_price, not mark-to-market:** the rules engine
  evaluates the total exposure cap (60%) at average entry price, not
  current market price — this underestimates real exposure when
  positions have appreciated (the same limitation noted since Phase 0,
  still not closed).
- **Missing stop re-arm:** a window where the stop is placed on the
  exchange but the process dies before persisting the stop id locally —
  on the next startup, the agent doesn't know that stop exists.
- **Missing explicit timeout in `LlmClient`:** the Anthropic SDK's default
  timeout can hold an entire cycle for ~30 minutes if a request hangs.
- **Missing post-response hardening in `LlmClient`:** a failure while
  processing the response *after* the call (outside the call's own `try`
  block) turns into a raw exception, with no specific handling.
- **`unenriched_news` processes oldest first:** under an enrichment
  backlog, a new piece of news can arrive and sit unenriched while older
  news is processed first.
- **Weekly review collect doesn't distinguish batch states:** an
  `errored`/`expired` batch is treated as `processing` (it keeps retrying
  indefinitely), and a `refusal` response from the model turns into an
  empty `learning` instead of being flagged as a failure.
- **1h system-prompt cache below the minimum cacheable prefix:** the
  system prompt has not yet reached the minimum size for Anthropic's
  prompt caching to take effect — today it's a silent no-op; it will
  start working on its own once the prompts grow, with no action needed.

See also §9 (future extensions that already have a scope decision, as
distinct from this list of debt left implicitly behind).
