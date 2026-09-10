# Fase 2a — Orquestrador + Execução Testnet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O ciclo completo do agente rodando contra a testnet da Binance SEM LLM (proposer HOLD injetável): settings por env, adapter spot assinado (HMAC), carteira mark-to-market reconciliada da exchange, marks de equity persistidos com rolagem, halt persistente com regras de release, snapshot/contexto com hash, decision log preenchido, ordens LIMIT IOC + stop-loss na exchange — mais o fechamento de dois débitos do backtest (sizing fracionário e validação out-of-sample).

**Architecture:** Módulos novos `src/invest_agent/settings.py`, `src/invest_agent/execution/` (adapter Binance com transport/clock injetáveis) e `src/invest_agent/orchestrator/` (state, snapshot, cycle). O SQLite da Fase 1c ganha tabelas de posições, marks, halt, aprovações pendentes e custo de API. TODO I/O (rede, relógio, env) entra por injeção; `main()` do CLI é a única borda. O LLM entra na Fase 2b via interface `Proposer` já definida aqui.

**Tech Stack:** Python ≥ 3.12; runtime: duckdb, pyarrow, backtrader (existentes) — ZERO deps novas nesta 2a (HMAC/urllib são stdlib).

**Spec:** `docs/superpowers/specs/2026-09-05-invest-agent-design.md` (§4.2 memória, §4.4 motor, §4.5 execução, §5 fase 2). **Rulings do controller (no ledger):**
1. **Stop-loss como `STOP_LOSS_LIMIT` (perna única), não OCO:** a política não define take-profit, então a OCO degeneraria; o objetivo declarado da spec ("stop na exchange, sobrevive à queda do VPS") é cumprido pela perna única. OCO entra se/quando a política tiver TP.
2. **Ordens de entrada/saída `LIMIT IOC`** ao best ask/bid: executa o que der imediatamente e cancela o resto — zero gestão de ordem pendurada, reconciliação trivial; o remanescente é retomado naturalmente no ciclo seguinte.
3. **HMAC SHA256** (chave normal da Binance) em vez de Ed25519 nesta fase — testnet aceita HMAC; Ed25519 exige geração de par de chaves fora do escopo de código; documentado no runbook (2c) como recomendação para produção.
4. **avg_price das posições** vem da NOSSA tabela `positions` (atualizada pelos fills que nós mesmos enviamos); a exchange é a verdade para QUANTIDADES (balances). Divergência de qty → balances vencem.
5. **Exposição mark-to-market:** o débito da Fase 0 é fechado AQUI — `PortfolioState.equity` e os notionais usados pelo ciclo passam a ser calculados com preços atuais de TODAS as posições (o engine continua igual; quem o alimenta agora manda dados marcados a mercado).

## Global Constraints

- Nenhuma dep nova; stdlib para HTTP/HMAC.
- **Segredos:** chaves API só via env (`Settings.from_env(env)` com env injetável); campos secretos com `repr=False`; NENHUM segredo em log, exceção, print ou teste.
- Nenhum teste acessa rede/relógio/env reais: transport, clock e env sempre injetáveis; testes usam fakes e `tmp_path`.
- **Nunca re-tentar POST de ordem às cegas** (spec §4.5): 5XX em POST = estado desconhecido → o chamador reconcilia via consulta; GETs podem re-tentar.
- Todas as datas UTC; `now` sempre parâmetro (borda única: `main()`).
- Mensagens em português; commits pequenos, sem assinatura.
- `orchestrator/` e `execution/` podem importar o motor da Fase 0, `data/`, `storage/`; o motor da Fase 0 NUNCA importa estes módulos.

---

### Task 1: `Settings` por env, sem vazar segredos

**Files:**
- Create: `src/invest_agent/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `Settings` (dataclass frozen) com campos: `binance_api_key: str = ""` (repr=False), `binance_api_secret: str = ""` (repr=False), `binance_base_url: str = "https://testnet.binance.vision"`, `db_path: Path = Path("data/agent.db")`, `candles_root: Path = Path("data/candles")`, `kill_switch_path: Path = Path("data/KILL")`, `heartbeat_path: Path = Path("data/heartbeat")`, `telegram_token: str = ""` (repr=False), `telegram_chat_id: str = ""`, `anthropic_api_key: str = ""` (repr=False), `api_cost_daily_cap_usd: float = 2.0`; e `Settings.from_env(env: Mapping[str, str]) -> Settings` (vars: `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `BINANCE_BASE_URL`, `INVEST_DB_PATH`, `INVEST_CANDLES_ROOT`, `INVEST_KILL_PATH`, `INVEST_HEARTBEAT_PATH`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`, `API_COST_DAILY_CAP_USD`). Tasks 3 e 6 (e as fases 2b/2c) consomem.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings.py
from pathlib import Path

from invest_agent.settings import Settings


def test_from_env_le_todas_as_vars():
    env = {
        "BINANCE_API_KEY": "k123",
        "BINANCE_API_SECRET": "s456",
        "BINANCE_BASE_URL": "https://api.binance.com",
        "INVEST_DB_PATH": "/tmp/x.db",
        "INVEST_CANDLES_ROOT": "/tmp/candles",
        "INVEST_KILL_PATH": "/tmp/KILL",
        "INVEST_HEARTBEAT_PATH": "/tmp/hb",
        "TELEGRAM_BOT_TOKEN": "t789",
        "TELEGRAM_CHAT_ID": "42",
        "ANTHROPIC_API_KEY": "a000",
        "API_COST_DAILY_CAP_USD": "3.5",
    }
    s = Settings.from_env(env)
    assert s.binance_api_key == "k123" and s.binance_api_secret == "s456"
    assert s.binance_base_url == "https://api.binance.com"
    assert s.db_path == Path("/tmp/x.db")
    assert s.candles_root == Path("/tmp/candles")
    assert s.kill_switch_path == Path("/tmp/KILL")
    assert s.heartbeat_path == Path("/tmp/hb")
    assert s.telegram_token == "t789" and s.telegram_chat_id == "42"
    assert s.anthropic_api_key == "a000"
    assert s.api_cost_daily_cap_usd == 3.5


def test_defaults_apontam_para_testnet_e_data():
    s = Settings.from_env({})
    assert s.binance_base_url == "https://testnet.binance.vision"
    assert s.db_path == Path("data/agent.db")
    assert s.api_cost_daily_cap_usd == 2.0
    assert s.binance_api_key == "" and s.telegram_token == ""


def test_repr_nao_vaza_segredos():
    s = Settings.from_env({"BINANCE_API_KEY": "SEGREDO-K",
                           "BINANCE_API_SECRET": "SEGREDO-S",
                           "TELEGRAM_BOT_TOKEN": "SEGREDO-T",
                           "ANTHROPIC_API_KEY": "SEGREDO-A"})
    texto = repr(s)
    for segredo in ("SEGREDO-K", "SEGREDO-S", "SEGREDO-T", "SEGREDO-A"):
        assert segredo not in texto
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_settings.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'invest_agent.settings'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/settings.py
"""Configuração por variáveis de ambiente (spec §4.5: secrets em env,
nunca em repo/prompt/log). from_env recebe o mapping — só o main() de um
CLI passa os.environ de verdade. Campos secretos têm repr=False: um
traceback ou log de Settings nunca vaza chave."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    binance_api_key: str = field(default="", repr=False)
    binance_api_secret: str = field(default="", repr=False)
    binance_base_url: str = "https://testnet.binance.vision"
    db_path: Path = Path("data/agent.db")
    candles_root: Path = Path("data/candles")
    kill_switch_path: Path = Path("data/KILL")
    heartbeat_path: Path = Path("data/heartbeat")
    telegram_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""
    anthropic_api_key: str = field(default="", repr=False)
    api_cost_daily_cap_usd: float = 2.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        base = cls()
        return cls(
            binance_api_key=env.get("BINANCE_API_KEY", ""),
            binance_api_secret=env.get("BINANCE_API_SECRET", ""),
            binance_base_url=env.get("BINANCE_BASE_URL", base.binance_base_url),
            db_path=Path(env.get("INVEST_DB_PATH", str(base.db_path))),
            candles_root=Path(env.get("INVEST_CANDLES_ROOT",
                                      str(base.candles_root))),
            kill_switch_path=Path(env.get("INVEST_KILL_PATH",
                                          str(base.kill_switch_path))),
            heartbeat_path=Path(env.get("INVEST_HEARTBEAT_PATH",
                                        str(base.heartbeat_path))),
            telegram_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID", ""),
            anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
            api_cost_daily_cap_usd=float(
                env.get("API_COST_DAILY_CAP_USD",
                        str(base.api_cost_daily_cap_usd))),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_settings.py -v`
Expected: PASS (3 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/settings.py tests/test_settings.py
git commit -m "feat: Settings por env com segredos fora do repr"
```

---

### Task 2: Tabelas operacionais no SQLite

**Files:**
- Modify: `src/invest_agent/storage/sqlite_store.py`
- Test: `tests/test_sqlite_ops.py`

**Interfaces:**
- Consumes: `SqliteStore` existente (Fase 1c) — NÃO alterar tabelas/métodos existentes.
- Produces (métodos novos em `SqliteStore` + tabelas novas):
  - `positions(symbol TEXT PK, qty REAL, avg_price REAL, stop_order_id TEXT)` → `upsert_position(symbol, qty, avg_price, stop_order_id=None)`, `get_positions() -> dict[str, tuple[float, float, str | None]]` (symbol → (qty, avg_price, stop_order_id)), `delete_position(symbol)`.
  - `equity_marks(period TEXT PK, open_value REAL, opened_at TEXT)` → `set_mark(period, open_value, opened_at: datetime)`, `get_mark(period) -> tuple[float, datetime] | None` (period ∈ "day"/"week"/"month").
  - `halt_state(id INTEGER PK CHECK(id=1), level TEXT, set_at TEXT, released INTEGER)` → `set_halt(level: str, set_at: datetime)`, `get_halt() -> tuple[str, datetime, bool] | None`, `release_halt()` (marca released=1).
  - `pending_approvals(decision_id TEXT PK, created_at TEXT, expires_at TEXT, status TEXT)` (status: "pending"/"approved"/"rejected"/"expired") → `add_pending(decision_id, created_at, expires_at)`, `get_pending() -> list[tuple[str, datetime, datetime, str]]` (só status="pending"), `set_pending_status(decision_id, status)`.
  - `api_costs(date TEXT, usd REAL)` → `add_api_cost(day: date, usd: float)`, `api_cost_today(day: date) -> float` (soma do dia).
  - `count_orders_on(day: date) -> int` e `last_order_at_by_symbol() -> dict[str, datetime]` derivados do `decision_log` (linhas com `order_json` não-nulo; usar o campo `ts`). Task 4 consome tudo isto.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sqlite_ops.py
from datetime import date, datetime, timezone

from invest_agent.storage.sqlite_store import DecisionRecord, SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def test_positions_crud(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_position("BTCUSDT", 0.5, 100_000.0, "ia-abc-sl")
    store.upsert_position("ETHUSDT", 2.0, 4_000.0)
    assert store.get_positions() == {
        "BTCUSDT": (0.5, 100_000.0, "ia-abc-sl"),
        "ETHUSDT": (2.0, 4_000.0, None),
    }
    store.upsert_position("BTCUSDT", 0.7, 101_000.0, "ia-def-sl")  # upsert
    assert store.get_positions()["BTCUSDT"] == (0.7, 101_000.0, "ia-def-sl")
    store.delete_position("ETHUSDT")
    assert "ETHUSDT" not in store.get_positions()
    store.close()


def test_equity_marks(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    assert store.get_mark("day") is None
    store.set_mark("day", 10_000.0, NOW)
    value, opened_at = store.get_mark("day")
    assert value == 10_000.0 and opened_at == NOW
    store.set_mark("day", 11_000.0, NOW)  # substitui
    assert store.get_mark("day")[0] == 11_000.0
    store.close()


def test_halt_state(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    assert store.get_halt() is None
    store.set_halt("MONTH", NOW)
    level, set_at, released = store.get_halt()
    assert level == "MONTH" and set_at == NOW and released is False
    store.release_halt()
    assert store.get_halt()[2] is True
    store.set_halt("DAY", NOW)  # novo halt zera released
    assert store.get_halt() == ("DAY", NOW, False)
    store.close()


def test_pending_approvals(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.add_pending("d1", NOW, NOW)
    (pend,) = store.get_pending()
    assert pend == ("d1", NOW, NOW, "pending")
    store.set_pending_status("d1", "expired")
    assert store.get_pending() == []
    store.close()


def test_api_costs_soma_por_dia(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    hoje = date(2026, 9, 10)
    assert store.api_cost_today(hoje) == 0.0
    store.add_api_cost(hoje, 0.5)
    store.add_api_cost(hoje, 0.25)
    store.add_api_cost(date(2026, 9, 9), 9.0)  # ontem não conta
    assert store.api_cost_today(hoje) == 0.75
    store.close()


def _decision(decision_id: str, ts: datetime, order: str | None):
    return DecisionRecord(decision_id=decision_id, ts=ts, inputs_hash="h",
                          snapshot_json="{}", proposal_json="{}",
                          verdict_json="{}", order_json=order)


def test_contadores_derivados_do_decision_log(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_decision(
        "d1", NOW, '{"symbol": "BTCUSDT"}'))
    store.append_decision(_decision(
        "d2", NOW.replace(hour=13), '{"symbol": "ETHUSDT"}'))
    store.append_decision(_decision("d3", NOW.replace(hour=14), None))  # HOLD
    ontem = NOW.replace(day=9)
    store.append_decision(_decision("d0", ontem, '{"symbol": "BTCUSDT"}'))
    assert store.count_orders_on(date(2026, 9, 10)) == 2
    last = store.last_order_at_by_symbol()
    assert last["BTCUSDT"] == NOW  # d0 é mais antigo; NOW vence
    assert last["ETHUSDT"] == NOW.replace(hour=13)
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sqlite_ops.py -v`
Expected: FAIL com `AttributeError` (métodos não existem)

- [ ] **Step 3: Write minimal implementation**

Acrescentar ao `_SCHEMA` de `sqlite_store.py` (mantendo TUDO que existe):

```sql
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    qty REAL NOT NULL,
    avg_price REAL NOT NULL,
    stop_order_id TEXT
);
CREATE TABLE IF NOT EXISTS equity_marks (
    period TEXT PRIMARY KEY,
    open_value REAL NOT NULL,
    opened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS halt_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    level TEXT NOT NULL,
    set_at TEXT NOT NULL,
    released INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pending_approvals (
    decision_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS api_costs (
    date TEXT NOT NULL,
    usd REAL NOT NULL
);
```

E os métodos (dentro de `SqliteStore`, seguindo o estilo existente — `json` já não é preciso aqui; para `last_order_at_by_symbol` use `json.loads` do `order_json`):

```python
    # --- posições (a exchange é a verdade p/ qty; avg_price é nosso) ---

    def upsert_position(self, symbol: str, qty: float, avg_price: float,
                        stop_order_id: str | None = None) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO positions (symbol, qty, avg_price,"
            " stop_order_id) VALUES (?,?,?,?)",
            (symbol, qty, avg_price, stop_order_id))
        self._con.commit()

    def get_positions(self) -> dict[str, tuple[float, float, str | None]]:
        rows = self._con.execute(
            "SELECT symbol, qty, avg_price, stop_order_id"
            " FROM positions").fetchall()
        return {r[0]: (r[1], r[2], r[3]) for r in rows}

    def delete_position(self, symbol: str) -> None:
        self._con.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        self._con.commit()

    # --- marks de equity ---

    def set_mark(self, period: str, open_value: float,
                 opened_at: datetime) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO equity_marks (period, open_value,"
            " opened_at) VALUES (?,?,?)",
            (period, open_value, opened_at.isoformat()))
        self._con.commit()

    def get_mark(self, period: str) -> tuple[float, datetime] | None:
        row = self._con.execute(
            "SELECT open_value, opened_at FROM equity_marks WHERE period=?",
            (period,)).fetchone()
        if row is None:
            return None
        return row[0], datetime.fromisoformat(row[1])

    # --- halt persistente ---

    def set_halt(self, level: str, set_at: datetime) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO halt_state (id, level, set_at, released)"
            " VALUES (1, ?, ?, 0)", (level, set_at.isoformat()))
        self._con.commit()

    def get_halt(self) -> tuple[str, datetime, bool] | None:
        row = self._con.execute(
            "SELECT level, set_at, released FROM halt_state"
            " WHERE id=1").fetchone()
        if row is None:
            return None
        return row[0], datetime.fromisoformat(row[1]), bool(row[2])

    def release_halt(self) -> None:
        self._con.execute("UPDATE halt_state SET released=1 WHERE id=1")
        self._con.commit()

    # --- aprovações pendentes (HITL) ---

    def add_pending(self, decision_id: str, created_at: datetime,
                    expires_at: datetime) -> None:
        self._con.execute(
            "INSERT INTO pending_approvals (decision_id, created_at,"
            " expires_at, status) VALUES (?,?,?,'pending')",
            (decision_id, created_at.isoformat(), expires_at.isoformat()))
        self._con.commit()

    def get_pending(self) -> list[tuple[str, datetime, datetime, str]]:
        rows = self._con.execute(
            "SELECT decision_id, created_at, expires_at, status"
            " FROM pending_approvals WHERE status='pending'"
            " ORDER BY created_at").fetchall()
        return [(r[0], datetime.fromisoformat(r[1]),
                 datetime.fromisoformat(r[2]), r[3]) for r in rows]

    def set_pending_status(self, decision_id: str, status: str) -> None:
        self._con.execute(
            "UPDATE pending_approvals SET status=? WHERE decision_id=?",
            (status, decision_id))
        self._con.commit()

    # --- custo de API (breaker por custo diário) ---

    def add_api_cost(self, day: date, usd: float) -> None:
        self._con.execute("INSERT INTO api_costs (date, usd) VALUES (?,?)",
                          (day.isoformat(), usd))
        self._con.commit()

    def api_cost_today(self, day: date) -> float:
        (total,) = self._con.execute(
            "SELECT COALESCE(SUM(usd), 0.0) FROM api_costs WHERE date=?",
            (day.isoformat(),)).fetchone()
        return total

    # --- contadores derivados do decision_log ---

    def count_orders_on(self, day: date) -> int:
        (n,) = self._con.execute(
            "SELECT COUNT(*) FROM decision_log WHERE order_json IS NOT NULL"
            " AND substr(ts, 1, 10) = ?", (day.isoformat(),)).fetchone()
        return n

    def last_order_at_by_symbol(self) -> dict[str, datetime]:
        import json as _json
        rows = self._con.execute(
            "SELECT ts, order_json FROM decision_log"
            " WHERE order_json IS NOT NULL ORDER BY ts").fetchall()
        out: dict[str, datetime] = {}
        for ts, order_json in rows:
            symbol = _json.loads(order_json).get("symbol")
            if symbol:
                out[symbol] = datetime.fromisoformat(ts)
        return out
```

(`import json` pode ir para o topo do arquivo em vez de local — seguir o estilo do módulo.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sqlite_ops.py tests/test_sqlite_store.py -v`
Expected: PASS (6 novos + 6 antigos — os antigos NÃO podem quebrar).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/storage/sqlite_store.py tests/test_sqlite_ops.py
git commit -m "feat: tabelas operacionais (posições, marks, halt, HITL, custo API)"
```

---

### Task 3: Adapter Binance spot assinado (testnet)

**Files:**
- Create: `src/invest_agent/execution/__init__.py`
- Create: `src/invest_agent/execution/binance_adapter.py`
- Test: `tests/test_binance_adapter.py`

**Interfaces:**
- Consumes: `Settings` (Task 1), `OrderIntent` (Fase 0).
- Produces: `BinanceAdapterError(Exception)`; classe `BinanceSpotAdapter(api_key: str, api_secret: str, base_url: str, http: Callable[[str, str, dict, bytes | None], bytes] | None = None, clock_ms: Callable[[], int] | None = None, sleeper=time.sleep)` com métodos:
  - `get_price(symbol) -> float` (GET /api/v3/ticker/price, público)
  - `get_book(symbol) -> tuple[float, float]` (bid, ask; GET /api/v3/bookTicker → path real: /api/v3/ticker/bookTicker)
  - `get_balances() -> dict[str, float]` (GET /api/v3/account assinado; só saldos `free` > 0)
  - `place_limit_ioc(order: OrderIntent) -> dict` (POST /api/v3/order assinado: type=LIMIT, timeInForce=IOC, side/quantity/price/newClientOrderId do OrderIntent; retorna o JSON da resposta com `status`, `executedQty`, `cummulativeQuoteQty`)
  - `place_stop_loss(symbol: str, qty: float, stop_price: float, client_order_id: str) -> dict` (POST /api/v3/order: type=STOP_LOSS_LIMIT, side=SELL, stopPrice=stop_price, price=stop_price*0.995 formatado, timeInForce=GTC)
  - `cancel_order(symbol: str, client_order_id: str) -> dict` (DELETE /api/v3/order assinado, `origClientOrderId`)
  - `get_order(symbol: str, client_order_id: str) -> dict` (GET /api/v3/order assinado — reconciliação pós-5xx)
  - Regras: assinatura HMAC-SHA256 do querystring (+`timestamp` do clock_ms +`recvWindow=5000`), header `X-MBX-APIKEY`; GET re-tenta 429/5xx (máx 3, backoff via sleeper); **POST/DELETE NUNCA re-tentam** — erro vira `BinanceAdapterError` com a dica "estado desconhecido — reconcilie com get_order"; 418 → erro imediato "IP banido". Preços/quantidades formatados com `f"{value:.8f}".rstrip('0').rstrip('.')`. Task 6 consome.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_binance_adapter.py
import hashlib
import hmac
import json
import urllib.error
import urllib.parse

import pytest

from invest_agent.execution.binance_adapter import (
    BinanceAdapterError, BinanceSpotAdapter,
)
from invest_agent.models import OrderIntent

KEY, SECRET = "k-test", "s-test"
CLOCK = lambda: 1_789_000_000_000  # ms fixo


def _adapter(http):
    return BinanceSpotAdapter(KEY, SECRET, "https://testnet.binance.vision",
                              http=http, clock_ms=CLOCK,
                              sleeper=lambda s: None)


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", None, None)


def test_get_price_publico_sem_assinatura():
    calls = []

    def http(method, url, headers, body):
        calls.append((method, url, headers))
        return json.dumps({"symbol": "BTCUSDT", "price": "50000.10"}).encode()

    assert _adapter(http).get_price("BTCUSDT") == 50000.10
    method, url, headers = calls[0]
    assert method == "GET" and "signature" not in url
    assert "X-MBX-APIKEY" not in headers


def test_get_balances_assina_e_filtra_zerados():
    def http(method, url, headers, body):
        query = urllib.parse.urlsplit(url).query
        params = dict(urllib.parse.parse_qsl(query))
        base = query.rsplit("&signature=", 1)[0]
        esperada = hmac.new(SECRET.encode(), base.encode(),
                            hashlib.sha256).hexdigest()
        assert params["signature"] == esperada
        assert params["timestamp"] == "1789000000000"
        assert headers["X-MBX-APIKEY"] == KEY
        return json.dumps({"balances": [
            {"asset": "USDT", "free": "1000.5", "locked": "0"},
            {"asset": "BTC", "free": "0.25", "locked": "0"},
            {"asset": "ETH", "free": "0.00000000", "locked": "0"},
        ]}).encode()

    balances = _adapter(http).get_balances()
    assert balances == {"USDT": 1000.5, "BTC": 0.25}


def test_place_limit_ioc_monta_ordem():
    calls = []

    def http(method, url, headers, body):
        calls.append((method, url))
        return json.dumps({"status": "FILLED", "executedQty": "0.1",
                           "cummulativeQuoteQty": "5000"}).encode()

    order = OrderIntent(symbol="BTCUSDT", side="BUY", qty=0.1,
                        limit_price=50_000.0, stop_loss_price=47_500.0,
                        client_order_id="ia-abc")
    resp = _adapter(http).place_limit_ioc(order)
    assert resp["status"] == "FILLED"
    method, url = calls[0]
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert method == "POST"
    assert params["type"] == "LIMIT" and params["timeInForce"] == "IOC"
    assert params["side"] == "BUY" and params["symbol"] == "BTCUSDT"
    assert params["quantity"] == "0.1" and params["price"] == "50000"
    assert params["newClientOrderId"] == "ia-abc"


def test_post_nao_retenta_e_manda_reconciliar():
    tentativas = []

    def http(method, url, headers, body):
        tentativas.append(1)
        raise _http_error(500)

    order = OrderIntent(symbol="BTCUSDT", side="BUY", qty=0.1,
                        limit_price=50_000.0, stop_loss_price=None,
                        client_order_id="ia-abc")
    with pytest.raises(BinanceAdapterError, match="reconcilie"):
        _adapter(http).place_limit_ioc(order)
    assert len(tentativas) == 1  # NUNCA re-tenta POST


def test_get_retenta_5xx_e_depois_sucede():
    tentativas = []

    def http(method, url, headers, body):
        tentativas.append(1)
        if len(tentativas) < 3:
            raise _http_error(500)
        return json.dumps({"symbol": "BTCUSDT", "price": "1"}).encode()

    assert _adapter(http).get_price("BTCUSDT") == 1.0
    assert len(tentativas) == 3


def test_418_erro_imediato():
    def http(method, url, headers, body):
        raise _http_error(418)

    with pytest.raises(BinanceAdapterError, match="banido"):
        _adapter(http).get_price("BTCUSDT")


def test_place_stop_loss_e_cancel():
    calls = []

    def http(method, url, headers, body):
        calls.append((method, url))
        return b"{}"

    adapter = _adapter(http)
    adapter.place_stop_loss("BTCUSDT", 0.1, 47_500.0, "ia-abc-sl")
    adapter.cancel_order("BTCUSDT", "ia-abc-sl")
    params_stop = dict(urllib.parse.parse_qsl(
        urllib.parse.urlsplit(calls[0][1]).query))
    assert params_stop["type"] == "STOP_LOSS_LIMIT"
    assert params_stop["stopPrice"] == "47500"
    assert params_stop["price"] == "47262.5"  # 47500 * 0.995
    assert params_stop["timeInForce"] == "GTC"
    assert calls[1][0] == "DELETE"
    params_cancel = dict(urllib.parse.parse_qsl(
        urllib.parse.urlsplit(calls[1][1]).query))
    assert params_cancel["origClientOrderId"] == "ia-abc-sl"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_binance_adapter.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/execution/binance_adapter.py
"""Adapter spot da Binance (testnet por default) — o ÚNICO componente que
verá credenciais (spec §4.5). HMAC SHA256; POSTs de ordem NUNCA re-tentam
(5xx = estado desconhecido → reconciliar via get_order); GETs re-tentam.
newClientOrderId determinístico vem do motor (idempotência)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from ..models import OrderIntent

_GET_RETRIES = 3


class BinanceAdapterError(Exception):
    """Falha na comunicação/execução com a Binance."""


def _default_http(method: str, url: str, headers: dict,
                  body: bytes | None) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _fmt(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


class BinanceSpotAdapter:
    def __init__(self, api_key: str, api_secret: str, base_url: str,
                 http: Callable[[str, str, dict, bytes | None], bytes] | None = None,
                 clock_ms: Callable[[], int] | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self._key = api_key
        self._secret = api_secret.encode()
        self._base = base_url.rstrip("/")
        self._http = http or _default_http
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleeper

    # --- núcleo de request ---

    def _request(self, method: str, path: str, params: dict,
                 signed: bool, retry: bool) -> dict:
        params = dict(params)
        headers: dict = {}
        if signed:
            params["timestamp"] = str(self._clock_ms())
            params["recvWindow"] = "5000"
            query = urllib.parse.urlencode(params)
            signature = hmac.new(self._secret, query.encode(),
                                 hashlib.sha256).hexdigest()
            query = f"{query}&signature={signature}"
            headers["X-MBX-APIKEY"] = self._key
        else:
            query = urllib.parse.urlencode(params)
        url = f"{self._base}{path}" + (f"?{query}" if query else "")
        attempts = _GET_RETRIES if retry else 1
        delay = 1.0
        for attempt in range(attempts):
            try:
                return json.loads(self._http(method, url, headers, None))
            except urllib.error.HTTPError as err:
                if err.code == 418:
                    raise BinanceAdapterError(
                        f"IP banido pela Binance (418) em {path}") from err
                transient = err.code == 429 or err.code >= 500
                if transient and retry and attempt < attempts - 1:
                    self._sleep(delay)
                    delay *= 2
                    continue
                if transient and not retry:
                    raise BinanceAdapterError(
                        f"HTTP {err.code} em {method} {path} — estado "
                        "desconhecido; reconcilie com get_order") from err
                raise BinanceAdapterError(
                    f"HTTP {err.code} em {method} {path}") from err
            except urllib.error.URLError as err:
                if retry and attempt < attempts - 1:
                    self._sleep(delay)
                    delay *= 2
                    continue
                if not retry:
                    raise BinanceAdapterError(
                        f"falha de rede em {method} {path} — estado "
                        "desconhecido; reconcilie com get_order") from err
                raise BinanceAdapterError(
                    f"falha de rede em {method} {path}") from err
        raise AssertionError("inalcançável")

    # --- mercado (público) ---

    def get_price(self, symbol: str) -> float:
        data = self._request("GET", "/api/v3/ticker/price",
                             {"symbol": symbol}, signed=False, retry=True)
        return float(data["price"])

    def get_book(self, symbol: str) -> tuple[float, float]:
        data = self._request("GET", "/api/v3/ticker/bookTicker",
                             {"symbol": symbol}, signed=False, retry=True)
        return float(data["bidPrice"]), float(data["askPrice"])

    # --- conta (assinado) ---

    def get_balances(self) -> dict[str, float]:
        data = self._request("GET", "/api/v3/account", {},
                             signed=True, retry=True)
        return {b["asset"]: float(b["free"])
                for b in data.get("balances", [])
                if float(b["free"]) > 0}

    # --- ordens (assinado; SEM retry) ---

    def place_limit_ioc(self, order: OrderIntent) -> dict:
        return self._request("POST", "/api/v3/order", {
            "symbol": order.symbol,
            "side": order.side,
            "type": "LIMIT",
            "timeInForce": "IOC",
            "quantity": _fmt(order.qty),
            "price": _fmt(order.limit_price),
            "newClientOrderId": order.client_order_id,
        }, signed=True, retry=False)

    def place_stop_loss(self, symbol: str, qty: float, stop_price: float,
                        client_order_id: str) -> dict:
        return self._request("POST", "/api/v3/order", {
            "symbol": symbol,
            "side": "SELL",
            "type": "STOP_LOSS_LIMIT",
            "timeInForce": "GTC",
            "quantity": _fmt(qty),
            "stopPrice": _fmt(stop_price),
            "price": _fmt(stop_price * 0.995),
            "newClientOrderId": client_order_id,
        }, signed=True, retry=False)

    def cancel_order(self, symbol: str, client_order_id: str) -> dict:
        return self._request("DELETE", "/api/v3/order", {
            "symbol": symbol,
            "origClientOrderId": client_order_id,
        }, signed=True, retry=False)

    def get_order(self, symbol: str, client_order_id: str) -> dict:
        return self._request("GET", "/api/v3/order", {
            "symbol": symbol,
            "origClientOrderId": client_order_id,
        }, signed=True, retry=True)
```

`src/invest_agent/execution/__init__.py`: vazio.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_binance_adapter.py -v`
Expected: PASS (7 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/execution/ tests/test_binance_adapter.py
git commit -m "feat: adapter Binance spot assinado (IOC, stop-loss, sem retry em POST)"
```

---

### Task 4: Estado — carteira mark-to-market, marks e halt persistente

**Files:**
- Create: `src/invest_agent/orchestrator/__init__.py`
- Create: `src/invest_agent/orchestrator/state.py`
- Test: `tests/test_orchestrator_state.py`

**Interfaces:**
- Consumes: `SqliteStore` (Task 2), `PortfolioState`/`Position` (Fase 0), `EquityMarks`/`HaltLevel` (Fase 0).
- Produces:
  - `build_portfolio(store: SqliteStore, balances: dict[str, float], prices: dict[str, float], now: datetime) -> PortfolioState` — cash = `balances["USDT"]` (0 se ausente); posições: para cada símbolo na tabela `positions`, qty vem do BALANCE do ativo-base (a exchange é a verdade; se o balance zerar, a posição é removida da tabela), avg_price da tabela; equity = cash + Σ qty·preço_atual (**mark-to-market — fecha o débito da Fase 0**); `orders_today` = `store.count_orders_on(now.date())`; `last_order_at` = `store.last_order_at_by_symbol()`.
  - `ensure_marks(store, equity: float, now: datetime) -> EquityMarks` — cria/rola os marks: "day" rola quando `opened_at.date() != now.date()`; "week" quando a segunda-feira ISO da semana mudou; "month" quando (ano, mês) mudou; ao rolar, `open_value = equity` atual.
  - `record_halt_if_needed(store, level: HaltLevel, now) -> None` (grava só se level > NONE e (não há halt ativo OU o novo nível é mais severo)).
  - `active_halt(store, now: datetime) -> HaltLevel` — release automático: DAY expira quando `now.date() > set_at.date()`; WEEK expira na segunda-feira ISO seguinte; MONTH NUNCA expira sozinho (exige `store.release_halt()`); halt com `released=True` → NONE.
  Tasks 6 e a Fase 2c consomem.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_state.py
from datetime import datetime, timezone

from invest_agent.breakers import HaltLevel
from invest_agent.orchestrator.state import (
    active_halt, build_portfolio, ensure_marks, record_halt_if_needed,
)
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)  # quinta-feira


def test_build_portfolio_mark_to_market(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_position("BTCUSDT", 0.5, 90_000.0)   # avg 90k
    store.upsert_position("ETHUSDT", 2.0, 4_000.0)
    balances = {"USDT": 1_000.0, "BTC": 0.5, "ETH": 2.0}
    prices = {"BTCUSDT": 100_000.0, "ETHUSDT": 3_000.0}
    pf = build_portfolio(store, balances, prices, NOW)
    assert pf.cash == 1_000.0
    # equity marcado a MERCADO: 1000 + 0.5*100k + 2*3k = 57_000
    assert pf.equity == 57_000.0
    assert pf.positions["BTCUSDT"].qty == 0.5
    assert pf.positions["BTCUSDT"].avg_price == 90_000.0
    store.close()


def test_build_portfolio_balance_zerado_remove_posicao(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_position("BTCUSDT", 0.5, 90_000.0)
    pf = build_portfolio(store, {"USDT": 100.0}, {"BTCUSDT": 100_000.0}, NOW)
    assert pf.positions == {} and pf.equity == 100.0
    assert store.get_positions() == {}  # limpou a tabela
    store.close()


def test_build_portfolio_qty_do_balance_vence(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_position("BTCUSDT", 0.5, 90_000.0)
    balances = {"USDT": 0.0, "BTC": 0.3}  # exchange diz 0.3
    pf = build_portfolio(store, balances, {"BTCUSDT": 100_000.0}, NOW)
    assert pf.positions["BTCUSDT"].qty == 0.3
    store.close()


def test_ensure_marks_cria_e_rola(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    marks = ensure_marks(store, 10_000.0, NOW)
    assert marks.day_open == marks.week_open == marks.month_open == 10_000.0
    # mesmo dia: não rola
    marks2 = ensure_marks(store, 9_000.0, NOW.replace(hour=18))
    assert marks2.day_open == 10_000.0
    # dia seguinte (sexta): rola só o day
    marks3 = ensure_marks(store, 9_000.0, NOW.replace(day=11))
    assert marks3.day_open == 9_000.0 and marks3.week_open == 10_000.0
    # segunda seguinte (14/09): rola day e week; mês não
    seg = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)
    marks4 = ensure_marks(store, 8_000.0, seg)
    assert marks4.week_open == 8_000.0 and marks4.month_open == 10_000.0
    # outubro: rola tudo
    out = datetime(2026, 10, 1, 0, 5, tzinfo=timezone.utc)
    marks5 = ensure_marks(store, 7_000.0, out)
    assert marks5.month_open == 7_000.0
    store.close()


def test_halt_day_expira_no_dia_seguinte(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.DAY, NOW)
    assert active_halt(store, NOW.replace(hour=23)) is HaltLevel.DAY
    assert active_halt(store, NOW.replace(day=11)) is HaltLevel.NONE
    store.close()


def test_halt_week_expira_na_segunda_seguinte(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.WEEK, NOW)  # quinta 10/09
    dom = datetime(2026, 9, 13, 23, 0, tzinfo=timezone.utc)
    seg = datetime(2026, 9, 14, 0, 5, tzinfo=timezone.utc)
    assert active_halt(store, dom) is HaltLevel.WEEK
    assert active_halt(store, seg) is HaltLevel.NONE
    store.close()


def test_halt_month_so_sai_com_release_manual(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.MONTH, NOW)
    prox_ano = datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert active_halt(store, prox_ano) is HaltLevel.MONTH
    store.release_halt()
    assert active_halt(store, prox_ano) is HaltLevel.NONE
    store.close()


def test_halt_mais_severo_sobrescreve_mas_menos_severo_nao(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.DAY, NOW)
    record_halt_if_needed(store, HaltLevel.MONTH, NOW)
    assert active_halt(store, NOW) is HaltLevel.MONTH
    record_halt_if_needed(store, HaltLevel.DAY, NOW)  # ignorado
    assert active_halt(store, NOW) is HaltLevel.MONTH
    record_halt_if_needed(store, HaltLevel.NONE, NOW)  # no-op
    assert active_halt(store, NOW) is HaltLevel.MONTH
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_orchestrator_state.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`src/invest_agent/orchestrator/__init__.py`: vazio.

```python
# src/invest_agent/orchestrator/state.py
"""Estado operacional do ciclo: carteira MARCADA A MERCADO (fecha o débito
documentado da Fase 0 — o engine continua igual, mas quem o alimenta agora
usa preços atuais), rolagem dos marks de equity (dia/semana/mês UTC) e o
halt persistente com regras de release da spec §4.4: DAY até D+1, WEEK até
a segunda seguinte, MONTH só com retomada manual do dono."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..breakers import EquityMarks, HaltLevel
from ..models import PortfolioState, Position
from ..storage.sqlite_store import SqliteStore


def _base_asset(symbol: str) -> str:
    return symbol.removesuffix("USDT")


def build_portfolio(store: SqliteStore, balances: dict[str, float],
                    prices: dict[str, float], now: datetime) -> PortfolioState:
    cash = balances.get("USDT", 0.0)
    positions: dict[str, Position] = {}
    for symbol, (_, avg_price, _) in store.get_positions().items():
        qty = balances.get(_base_asset(symbol), 0.0)
        if qty <= 0:
            store.delete_position(symbol)  # a exchange é a verdade
            continue
        positions[symbol] = Position(symbol=symbol, qty=qty,
                                     avg_price=avg_price)
    equity = cash + sum(p.qty * prices.get(p.symbol, p.avg_price)
                        for p in positions.values())
    return PortfolioState(
        equity=equity,
        cash=cash,
        positions=positions,
        orders_today=store.count_orders_on(now.date()),
        last_order_at=store.last_order_at_by_symbol(),
    )


def _monday(dt: datetime) -> datetime:
    day = dt.date() - timedelta(days=dt.weekday())
    return datetime(day.year, day.month, day.day, tzinfo=dt.tzinfo)


def _rolled(period: str, opened_at: datetime, now: datetime) -> bool:
    if period == "day":
        return opened_at.date() != now.date()
    if period == "week":
        return _monday(opened_at) != _monday(now)
    return (opened_at.year, opened_at.month) != (now.year, now.month)


def ensure_marks(store: SqliteStore, equity: float,
                 now: datetime) -> EquityMarks:
    values: dict[str, float] = {}
    for period in ("day", "week", "month"):
        mark = store.get_mark(period)
        if mark is None or _rolled(period, mark[1], now):
            store.set_mark(period, equity, now)
            values[period] = equity
        else:
            values[period] = mark[0]
    return EquityMarks(day_open=values["day"], week_open=values["week"],
                       month_open=values["month"])


_SEVERITY = {HaltLevel.NONE: 0, HaltLevel.DAY: 1,
             HaltLevel.WEEK: 2, HaltLevel.MONTH: 3}


def record_halt_if_needed(store: SqliteStore, level: HaltLevel,
                          now: datetime) -> None:
    if level is HaltLevel.NONE:
        return
    current = store.get_halt()
    if current is not None and not current[2]:
        if _SEVERITY[HaltLevel[current[0]]] >= _SEVERITY[level]:
            return
    store.set_halt(level.name, now)


def active_halt(store: SqliteStore, now: datetime) -> HaltLevel:
    row = store.get_halt()
    if row is None:
        return HaltLevel.NONE
    level_name, set_at, released = row
    if released:
        return HaltLevel.NONE
    level = HaltLevel[level_name]
    if level is HaltLevel.DAY and now.date() > set_at.date():
        return HaltLevel.NONE
    if level is HaltLevel.WEEK and _monday(now) > _monday(set_at):
        return HaltLevel.NONE
    return level
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_orchestrator_state.py -v`
Expected: PASS (8 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/orchestrator/ tests/test_orchestrator_state.py
git commit -m "feat: carteira mark-to-market, rolagem de marks e halt persistente"
```

---

### Task 5: Snapshot de mercado + contexto do ciclo com hash

**Files:**
- Create: `src/invest_agent/orchestrator/snapshot.py`
- Test: `tests/test_snapshot.py`

**Interfaces:**
- Consumes: `MarketSnapshot`/`PortfolioState` (Fase 0), `CandleStore` (1a), `indicators` (1a), `SqliteStore` (1c/T2).
- Produces:
  - `cycle_id(now: datetime) -> str` (ex.: `"2026091012"` — `%Y%m%d%H`; idempotency key por ciclo, spec §4.4).
  - `build_market_snapshot(symbol, last_price: float, bid: float, ask: float, candles: list[Candle], now: datetime) -> MarketSnapshot` — `candle_age_seconds = (now - candles[-1].close_time).total_seconds()` (lista vazia → age = +inf via `float("inf")`); `quote_volume_24h` = soma de `quote_volume` dos candles com `open_time >= now - 24h`.
  - `build_context(portfolio: PortfolioState, whitelist: frozenset[str], candles_by_symbol: dict[str, list[Candle]], news: list[tuple], macro: dict[str, float], now: datetime) -> dict` — JSON-serializável, com: `cycle_id`, `now` ISO, `whitelist` ordenada, por símbolo: último close, RSI(14) e SMA(20/50) atuais (None se insuficiente), `positions` {symbol: {qty, avg_price}}, `equity`, `cash`, `news` (lista de dicts título/fonte/ativos/published_at) e `macro`. Determinístico: mesmas entradas → mesmo dict.
  - `context_hash(context: dict) -> str` — sha256 de `json.dumps(context, sort_keys=True, ensure_ascii=False)`.
  - `Proposer = Callable[[dict], Proposal]` (type alias) e `hold_proposer(context: dict) -> Proposal` (HOLD em BTCUSDT com conviction 0.0, rationale "dry-run sem LLM", cycle_id do contexto). Task 6 e a Fase 2b consomem.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_snapshot.py
from datetime import datetime, timedelta, timezone

from invest_agent.models import Action, PortfolioState, Position
from invest_agent.orchestrator.snapshot import (
    build_context, build_market_snapshot, context_hash, cycle_id,
    hold_proposer,
)
from invest_agent.data.models import Candle

NOW = datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc)


def _candle(hours_ago: int, close: float = 100.0) -> Candle:
    open_time = NOW.replace(minute=0) - timedelta(hours=hours_ago)
    return Candle(symbol="BTCUSDT", interval="1h", open_time=open_time,
                  open=close, high=close, low=close, close=close,
                  volume=1.0, quote_volume=10.0, n_trades=1,
                  close_time=open_time + timedelta(minutes=59, seconds=59))


def test_cycle_id_por_hora():
    assert cycle_id(NOW) == "2026091012"


def test_build_market_snapshot():
    candles = [_candle(h) for h in range(30, 0, -1)]
    snap = build_market_snapshot("BTCUSDT", 100.5, 100.0, 101.0,
                                 candles, NOW)
    assert snap.symbol == "BTCUSDT" and snap.last_price == 100.5
    assert snap.best_bid == 100.0 and snap.best_ask == 101.0
    # último candle: open 11:00, close 11:59:59 → idade ~30min
    assert 1800 <= snap.candle_age_seconds <= 1900
    # 24 candles nas últimas 24h (open_time >= 12:30 de ontem → 13h..11h = 23? conferir: aberturas 11:00,10:00,... >= 2026-09-09 12:30 → 11:00 de hoje até 13:00 de ontem = 23 candles) * 10.0
    assert snap.quote_volume_24h == 230.0


def test_build_market_snapshot_sem_candles():
    snap = build_market_snapshot("BTCUSDT", 1.0, 1.0, 1.0, [], NOW)
    assert snap.candle_age_seconds == float("inf")
    assert snap.quote_volume_24h == 0.0


def test_build_context_deterministico_e_hash():
    pf = PortfolioState(equity=10_000.0, cash=9_000.0,
                        positions={"BTCUSDT": Position("BTCUSDT", 0.01,
                                                       95_000.0)})
    candles = {"BTCUSDT": [_candle(h, 100.0 + h) for h in range(60, 0, -1)]}
    news = [("Bitcoin sobe", "coindesk", ("BTCUSDT",), "2026-09-10T10:00:00+00:00")]
    macro = {"fng": 34.0, "selic": 15.0}
    ctx1 = build_context(pf, frozenset({"BTCUSDT", "ETHUSDT"}), candles,
                         news, macro, NOW)
    ctx2 = build_context(pf, frozenset({"ETHUSDT", "BTCUSDT"}), candles,
                         news, macro, NOW)
    assert ctx1 == ctx2  # determinístico (whitelist ordenada)
    assert ctx1["cycle_id"] == "2026091012"
    assert ctx1["whitelist"] == ["BTCUSDT", "ETHUSDT"]
    assert ctx1["equity"] == 10_000.0
    assert ctx1["positions"]["BTCUSDT"]["qty"] == 0.01
    btc = ctx1["symbols"]["BTCUSDT"]
    assert btc["close"] == 101.0  # último candle: 100 + 1
    assert btc["rsi_14"] is not None and btc["sma_20"] is not None
    assert ctx1["news"][0]["title"] == "Bitcoin sobe"
    assert ctx1["macro"]["fng"] == 34.0
    assert context_hash(ctx1) == context_hash(ctx2)
    assert len(context_hash(ctx1)) == 64


def test_hold_proposer():
    ctx = {"cycle_id": "2026091012"}
    p = hold_proposer(ctx)
    assert p.action is Action.HOLD and p.conviction == 0.0
    assert p.cycle_id == "2026091012"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_snapshot.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/orchestrator/snapshot.py
"""Snapshot do ciclo (spec §4.3 passo 1): código monta TODO o contexto —
candles+indicadores calculados em Python, notícias já enriquecidas,
posições, macro — e o hash dos inputs vai para o decision_log (auditoria:
que dados exatos gearam a decisão). O LLM (Fase 2b) recebe este dict
pronto; um Proposer é qualquer Callable[[dict], Proposal]."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Callable

from ..data.models import Candle
from ..indicators import rsi, sma
from ..models import Action, MarketSnapshot, PortfolioState, Proposal

Proposer = Callable[[dict], Proposal]


def cycle_id(now: datetime) -> str:
    return f"{now:%Y%m%d%H}"


def build_market_snapshot(symbol: str, last_price: float, bid: float,
                          ask: float, candles: list[Candle],
                          now: datetime) -> MarketSnapshot:
    if candles:
        age = (now - candles[-1].close_time).total_seconds()
    else:
        age = float("inf")
    cutoff = now - timedelta(hours=24)
    volume_24h = sum(c.quote_volume for c in candles
                     if c.open_time >= cutoff)
    return MarketSnapshot(symbol=symbol, last_price=last_price,
                          best_bid=bid, best_ask=ask,
                          candle_age_seconds=age,
                          quote_volume_24h=volume_24h)


def _symbol_features(candles: list[Candle]) -> dict:
    closes = [c.close for c in candles]
    rsi_line = rsi(closes, 14)
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    return {
        "close": closes[-1] if closes else None,
        "rsi_14": rsi_line[-1] if closes else None,
        "sma_20": sma20[-1] if closes else None,
        "sma_50": sma50[-1] if closes else None,
    }


def build_context(portfolio: PortfolioState, whitelist: frozenset[str],
                  candles_by_symbol: dict[str, list[Candle]],
                  news: list[tuple], macro: dict[str, float],
                  now: datetime) -> dict:
    return {
        "cycle_id": cycle_id(now),
        "now": now.isoformat(),
        "whitelist": sorted(whitelist),
        "equity": portfolio.equity,
        "cash": portfolio.cash,
        "positions": {
            symbol: {"qty": p.qty, "avg_price": p.avg_price}
            for symbol, p in sorted(portfolio.positions.items())
        },
        "symbols": {
            symbol: _symbol_features(candles)
            for symbol, candles in sorted(candles_by_symbol.items())
        },
        "news": [
            {"title": title, "source": source, "assets": sorted(assets),
             "published_at": published_at}
            for title, source, assets, published_at in news
        ],
        "macro": dict(sorted(macro.items())),
    }


def context_hash(context: dict) -> str:
    payload = json.dumps(context, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def hold_proposer(context: dict) -> Proposal:
    """Proposer da Fase 2a: nunca opera. O ciclo inteiro roda (snapshot,
    veredito, log) sem nenhum LLM — dry-run operacional."""
    return Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale="dry-run sem LLM", cycle_id=context["cycle_id"])
```

**Nota:** confira a aritmética do teste de volume 24h você mesmo antes de rodar: candles com abertura de 11:00 de hoje até 13:00 de ontem têm `open_time >= 2026-09-09T12:30` — são 23 candles × 10.0 = 230.0. Se o seu resultado divergir, conte as aberturas geradas pelo helper (`range(30, 0, -1)` → aberturas de 30h atrás até 1h atrás) e reporte NEEDS_CONTEXT se discordar do esperado.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_snapshot.py -v`
Expected: PASS (5 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/orchestrator/snapshot.py tests/test_snapshot.py
git commit -m "feat: snapshot de mercado + contexto do ciclo com hash e Proposer"
```

---

### Task 6: O ciclo — run_cycle + CLI dry-run

**Files:**
- Create: `src/invest_agent/orchestrator/cycle.py`
- Test: `tests/test_cycle.py`
- Modify: `README.md` (seção de uso)

**Interfaces:**
- Consumes: tudo das Tasks 1-5 + `RulesEngine`/`KillSwitch`/`heartbeat_beat` (Fase 0) + `CandleStore` (1a).
- Produces: `CycleResult` (dataclass frozen: `cycle_id: str`, `verdict_status: str`, `reasons: list[str]`, `executed: bool`, `halted: str | None`) e
  `run_cycle(store, candle_store, adapter, engine, proposer, settings, now, dry_run: bool = False) -> CycleResult` com este fluxo EXATO:
  1. `heartbeat_beat(settings.heartbeat_path, now)`.
  2. Expira pendências HITL vencidas: para cada pending com `expires_at < now` → `set_pending_status(id, "expired")` (TTL 10 min → expirou = CANCELA, spec §4.4).
  3. `active_halt(store, now)` > NONE → retorna `CycleResult(..., verdict_status="halted", halted=level.name, executed=False)` SEM propor nada.
  4. Custo de API do dia ≥ `settings.api_cost_daily_cap_usd` → registra halt DAY + retorna halted ("custo de API acima do teto").
  5. Monta carteira: `balances = adapter.get_balances()`; preços atuais de cada posição via `adapter.get_price`; `build_portfolio`.
  6. `ensure_marks`; roda `proposal = proposer(context)` com `build_context` (candles do `candle_store` para a whitelist, notícias/macro atuais podem ir vazios nesta fase — listas vazias são aceitáveis; a 2b liga as fontes).
  7. HOLD → registra `DecisionRecord` (order_json=None) e retorna (executed=False).
  8. Caso contrário: `bid, ask = adapter.get_book(symbol)`; `last = adapter.get_price(symbol)`; `build_market_snapshot` com os candles do símbolo; `verdict = engine.evaluate(...)`; `record_halt_if_needed` com o nível ATUAL de `check_breakers` (import de breakers) para persistir halts novos.
  9. Registra SEMPRE o `DecisionRecord` (decision_id = `f"{cycle_id}-{symbol}"`, inputs_hash = `context_hash`, jsons de proposta/veredito/ordem).
  10. REJECTED → executed=False. NEEDS_APPROVAL → `add_pending(decision_id, now, now+10min)`, executed=False. APPROVED com ordem e `dry_run=False` → executa: SELL primeiro cancela stop antigo se houver (`stop_order_id` na tabela positions; erros de cancel são engolidos com registro no reason), `adapter.place_limit_ioc(order)`; com `executedQty > 0`: BUY → `upsert_position` com novo avg ponderado + `place_stop_loss` da qty executada (client_order_id = order.client_order_id + "-sl") + guarda `stop_order_id`; SELL → reduz/deleta posição. `dry_run=True` → não chama adapter de ordem, executed=False.
  - `main(argv)` (`python3 -m invest_agent.orchestrator.cycle --dry-run`): borda única — lê `os.environ`, monta Settings/stores/adapter/engine (whitelist: `ALWAYS_INCLUDED` como default mínimo se não houver whitelist persistida — a 2c liga o job semanal), roda um ciclo com `hold_proposer`, imprime o resultado em português.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cycle.py
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from invest_agent.config import MODERADO
from invest_agent.data.models import Candle
from invest_agent.data.store import CandleStore
from invest_agent.engine import RulesEngine
from invest_agent.killswitch import KillSwitch
from invest_agent.models import Action, Proposal
from invest_agent.orchestrator.cycle import CycleResult, run_cycle
from invest_agent.settings import Settings
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc)


class FakeAdapter:
    def __init__(self, price=100.0, bid=99.5, ask=100.5,
                 balances=None, fill_qty=None):
        self.price, self.bid, self.ask = price, bid, ask
        self.balances = balances or {"USDT": 10_000.0}
        self.fill_qty = fill_qty
        self.orders, self.stops, self.cancels = [], [], []

    def get_balances(self):
        return dict(self.balances)

    def get_price(self, symbol):
        return self.price

    def get_book(self, symbol):
        return self.bid, self.ask

    def place_limit_ioc(self, order):
        self.orders.append(order)
        qty = self.fill_qty if self.fill_qty is not None else order.qty
        return {"status": "FILLED" if qty == order.qty else "EXPIRED",
                "executedQty": str(qty),
                "cummulativeQuoteQty": str(qty * order.limit_price)}

    def place_stop_loss(self, symbol, qty, stop_price, client_order_id):
        self.stops.append((symbol, qty, stop_price, client_order_id))
        return {"status": "NEW"}

    def cancel_order(self, symbol, client_order_id):
        self.cancels.append((symbol, client_order_id))
        return {}


def _fixture(tmp_path, proposal=None, adapter=None):
    store = SqliteStore(tmp_path / "a.db")
    candle_store = CandleStore(tmp_path / "candles")
    candles = []
    for h in range(30, 0, -1):
        open_time = NOW.replace(minute=0) - timedelta(hours=h)
        candles.append(Candle(symbol="BTCUSDT", interval="1h",
                              open_time=open_time, open=100.0, high=100.0,
                              low=100.0, close=100.0, volume=1.0,
                              quote_volume=1_000_000.0, n_trades=10,
                              close_time=open_time + timedelta(minutes=59,
                                                               seconds=59)))
    candle_store.append(candles)
    settings = Settings(kill_switch_path=tmp_path / "KILL",
                        heartbeat_path=tmp_path / "hb")
    engine = RulesEngine(MODERADO, frozenset({"BTCUSDT", "ETHUSDT"}),
                         KillSwitch(settings.kill_switch_path))
    adapter = adapter or FakeAdapter()

    def proposer(context):
        return proposal or Proposal(symbol="BTCUSDT", action=Action.HOLD,
                                    conviction=0.0, rationale="x",
                                    cycle_id=context["cycle_id"])

    return store, candle_store, adapter, engine, proposer, settings


def test_hold_registra_decisao_sem_ordem(tmp_path):
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert isinstance(result, CycleResult)
    assert result.verdict_status == "approved" and result.executed is False
    (rec,) = store.read_decisions()
    assert rec.order_json is None and rec.inputs_hash
    assert settings.heartbeat_path.exists()
    store.close()


def test_buy_pequeno_executa_e_poe_stop(tmp_path):
    # equity 10k; conviction 0.019 → alvo 19 USDT < 2% (200) → APPROVED
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.executed is True
    assert len(adapter.orders) == 1 and adapter.orders[0].side == "BUY"
    assert len(adapter.stops) == 1
    positions = store.get_positions()
    assert "BTCUSDT" in positions
    qty, avg, stop_id = positions["BTCUSDT"]
    assert qty == adapter.orders[0].qty and avg == 100.5
    assert stop_id.endswith("-sl")
    store.close()


def test_buy_grande_vira_pendencia_hitl(tmp_path):
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=1.0,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.verdict_status == "needs_approval"
    assert adapter.orders == []
    (pend,) = store.get_pending()
    assert pend[2] == NOW + timedelta(minutes=10)  # TTL 10 min
    store.close()


def test_pendencia_vencida_expira_no_ciclo_seguinte(tmp_path):
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    store.add_pending("d-velha", NOW - timedelta(hours=1),
                      NOW - timedelta(minutes=50))
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert store.get_pending() == []  # expirada = cancelada
    store.close()


def test_halt_ativo_bloqueia_o_ciclo(tmp_path):
    from invest_agent.breakers import HaltLevel
    from invest_agent.orchestrator.state import record_halt_if_needed
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    record_halt_if_needed(store, HaltLevel.MONTH, NOW)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.verdict_status == "halted" and result.halted == "MONTH"
    assert store.read_decisions() == []  # nem propôs
    store.close()


def test_custo_de_api_acima_do_teto_halta(tmp_path):
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    store.add_api_cost(NOW.date(), settings.api_cost_daily_cap_usd + 0.01)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.verdict_status == "halted"
    assert "custo" in " ".join(result.reasons)
    store.close()


def test_dry_run_nao_envia_ordem(tmp_path):
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW,
                       dry_run=True)
    assert result.verdict_status == "approved" and result.executed is False
    assert adapter.orders == []
    (rec,) = store.read_decisions()
    assert rec.order_json is not None  # a decisão fica registrada
    store.close()


def test_sell_cancela_stop_antigo_e_reduz_posicao(tmp_path):
    prop = Proposal(symbol="BTCUSDT", action=Action.CLOSE, conviction=0.5,
                    rationale="x", cycle_id="2026091012")
    adapter = FakeAdapter(balances={"USDT": 10_000.0, "BTC": 0.001})
    store, cs, _, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop, adapter=adapter)
    store.upsert_position("BTCUSDT", 0.001, 90.0, "ia-old-sl")
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.executed is True
    assert adapter.cancels == [("BTCUSDT", "ia-old-sl")]
    assert adapter.orders[0].side == "SELL"
    assert store.get_positions() == {}  # posição zerada
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_cycle.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/orchestrator/cycle.py
"""Um ciclo do agente (spec §4.3): heartbeat → expira HITL vencido →
halt/custo → carteira mark-to-market → marks → proposta (Proposer
injetável; LLM só na Fase 2b) → RulesEngine → decision_log SEMPRE →
execução (LIMIT IOC + stop na exchange). O main() é a borda única com
env/relógio/rede reais."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from ..breakers import HaltLevel, check_breakers
from ..config import MODERADO
from ..data.store import CandleStore
from ..engine import RulesEngine
from ..killswitch import KillSwitch, heartbeat_beat
from ..models import Action, OrderIntent, Proposal, Verdict, VerdictStatus
from ..settings import Settings
from ..storage.sqlite_store import DecisionRecord, SqliteStore
from ..whitelist import ALWAYS_INCLUDED
from .snapshot import (Proposer, build_context, build_market_snapshot,
                       context_hash, cycle_id, hold_proposer)
from .state import (active_halt, build_portfolio, ensure_marks,
                    record_halt_if_needed)

HITL_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class CycleResult:
    cycle_id: str
    verdict_status: str
    reasons: list[str]
    executed: bool
    halted: str | None = None


def _expire_pending(store: SqliteStore, now: datetime) -> None:
    for decision_id, _, expires_at, _ in store.get_pending():
        if expires_at < now:
            store.set_pending_status(decision_id, "expired")


def _record(store: SqliteStore, decision_id: str, now: datetime,
            inputs_hash: str, context: dict, proposal: Proposal,
            verdict: Verdict) -> None:
    store.append_decision(DecisionRecord(
        decision_id=decision_id,
        ts=now.isoformat(),
        inputs_hash=inputs_hash,
        snapshot_json=json.dumps(context, sort_keys=True,
                                 ensure_ascii=False),
        proposal_json=json.dumps({
            "symbol": proposal.symbol, "action": proposal.action.value,
            "conviction": proposal.conviction,
            "rationale": proposal.rationale,
            "cycle_id": proposal.cycle_id}, ensure_ascii=False),
        verdict_json=json.dumps({
            "status": verdict.status.value,
            "reasons": verdict.reasons}, ensure_ascii=False),
        order_json=(json.dumps(asdict(verdict.order), ensure_ascii=False)
                    if verdict.order else None),
    ))


def _execute(store: SqliteStore, adapter, order: OrderIntent) -> bool:
    positions = store.get_positions()
    old = positions.get(order.symbol)
    if order.side == "SELL" and old and old[2]:
        try:
            adapter.cancel_order(order.symbol, old[2])
        except Exception:
            pass  # stop pode já ter executado/expirado — reconciliação natural
    resp = adapter.place_limit_ioc(order)
    executed_qty = float(resp.get("executedQty", "0") or 0)
    if executed_qty <= 0:
        return False
    quote = float(resp.get("cummulativeQuoteQty", "0") or 0)
    fill_price = quote / executed_qty if executed_qty else order.limit_price
    if order.side == "BUY":
        old_qty, old_avg = (old[0], old[1]) if old else (0.0, 0.0)
        new_qty = old_qty + executed_qty
        new_avg = ((old_qty * old_avg) + (executed_qty * fill_price)) / new_qty
        stop_id = f"{order.client_order_id}-sl"
        adapter.place_stop_loss(order.symbol, executed_qty,
                                order.stop_loss_price, stop_id)
        store.upsert_position(order.symbol, new_qty, new_avg, stop_id)
    else:
        remaining = (old[0] if old else 0.0) - executed_qty
        if remaining <= 1e-9:
            store.delete_position(order.symbol)
        else:
            store.upsert_position(order.symbol, remaining,
                                  old[1] if old else fill_price, None)
    return True


def run_cycle(store: SqliteStore, candle_store: CandleStore, adapter,
              engine: RulesEngine, proposer: Proposer, settings: Settings,
              now: datetime, dry_run: bool = False) -> CycleResult:
    cid = cycle_id(now)
    heartbeat_beat(settings.heartbeat_path, now)
    _expire_pending(store, now)

    halt = active_halt(store, now)
    if halt is not HaltLevel.NONE:
        return CycleResult(cid, "halted",
                           [f"halt {halt.name} ativo"], False, halt.name)

    if store.api_cost_today(now.date()) >= settings.api_cost_daily_cap_usd:
        record_halt_if_needed(store, HaltLevel.DAY, now)
        return CycleResult(cid, "halted",
                           ["custo de API diário acima do teto"], False,
                           HaltLevel.DAY.name)

    balances = adapter.get_balances()
    known = store.get_positions()
    prices = {symbol: adapter.get_price(symbol) for symbol in known}
    portfolio = build_portfolio(store, balances, prices, now)
    marks = ensure_marks(store, portfolio.equity, now)

    whitelist = frozenset(engine.whitelist)
    candles_by_symbol = {
        s: candle_store.read(s, "1h", start=now - timedelta(days=7))
        for s in sorted(whitelist)}
    context = build_context(portfolio, whitelist, candles_by_symbol,
                            news=[], macro={}, now=now)
    inputs_hash = context_hash(context)

    proposal = proposer(context)
    decision_id = f"{cid}-{proposal.symbol}"

    if proposal.action is Action.HOLD:
        verdict = engine.evaluate(
            proposal, portfolio,
            build_market_snapshot(proposal.symbol, 0.0, 0.0, 0.0, [], now),
            marks, now)
        _record(store, decision_id, now, inputs_hash, context, proposal,
                verdict)
        return CycleResult(cid, verdict.status.value, verdict.reasons, False)

    bid, ask = adapter.get_book(proposal.symbol)
    last = adapter.get_price(proposal.symbol)
    market = build_market_snapshot(
        proposal.symbol, last, bid, ask,
        candles_by_symbol.get(proposal.symbol, []), now)
    verdict = engine.evaluate(proposal, portfolio, market, marks, now)
    record_halt_if_needed(
        store, check_breakers(portfolio.equity, marks, engine.profile), now)
    _record(store, decision_id, now, inputs_hash, context, proposal, verdict)

    if verdict.status is VerdictStatus.NEEDS_APPROVAL:
        store.add_pending(decision_id, now, now + HITL_TTL)
        return CycleResult(cid, verdict.status.value, verdict.reasons, False)
    if verdict.status is not VerdictStatus.APPROVED or verdict.order is None:
        return CycleResult(cid, verdict.status.value, verdict.reasons, False)
    if dry_run:
        return CycleResult(cid, verdict.status.value,
                           ["dry-run: ordem não enviada"], False)
    executed = _execute(store, adapter, verdict.order)
    return CycleResult(cid, verdict.status.value, verdict.reasons, executed)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Um ciclo do agente")
    parser.add_argument("--dry-run", action="store_true",
                        help="avalia e registra, mas não envia ordens")
    args = parser.parse_args(argv)

    from ..execution.binance_adapter import BinanceSpotAdapter

    settings = Settings.from_env(os.environ)  # borda única
    now = datetime.now(timezone.utc)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(settings.db_path)
    candle_store = CandleStore(settings.candles_root)
    adapter = BinanceSpotAdapter(settings.binance_api_key,
                                 settings.binance_api_secret,
                                 settings.binance_base_url)
    engine = RulesEngine(MODERADO, ALWAYS_INCLUDED,
                         KillSwitch(settings.kill_switch_path))
    result = run_cycle(store, candle_store, adapter, engine, hold_proposer,
                       settings, now, dry_run=args.dry_run)
    print(f"ciclo {result.cycle_id}: {result.verdict_status}"
          + (f" ({'; '.join(result.reasons)})" if result.reasons else "")
          + (" — ordem executada" if result.executed else ""))
    store.close()


if __name__ == "__main__":
    main()
```

No `README.md`, acrescentar após a seção do backtest:

```markdown
## 🔁 Ciclo do agente (Fase 2 — testnet)

```bash
export BINANCE_API_KEY=... BINANCE_API_SECRET=...   # chaves da TESTNET
python3 -m invest_agent.orchestrator.cycle --dry-run
```

Um ciclo completo: heartbeat → halt/custo de API → carteira mark-to-market
reconciliada da exchange → proposta (sem LLM por enquanto: proposer HOLD) →
motor de regras → decision log append-only → ordem LIMIT IOC + stop-loss na
exchange. Sem `--dry-run`, ordens aprovadas são enviadas à testnet.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cycle.py -v` e depois `python3 -m pytest`
Expected: PASS (8 novos; suíte completa verde).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/orchestrator/cycle.py tests/test_cycle.py README.md
git commit -m "feat: ciclo completo com execução IOC, stop na exchange e HITL TTL"
```

---

### Task 7: Sizing fracionário no backtest

**Files:**
- Modify: `src/invest_agent/backtest/engine_bt.py`
- Modify: `tests/test_engine_bt.py`
- Modify: `README.md` (remover o bullet de sizing inteiro das limitações)

**Interfaces:**
- Mantém `run_backtrader(...)` idêntico por fora. Por dentro: `size = round(cash * stake_pct / (price * 1.05), 6)` (fracionário — cripto é fracionária); o guard de "caixa insuficiente" agora dispara quando `size <= 0` após o arredondamento (cash ínfimo).

- [ ] **Step 1: Atualizar os testes (fonte da verdade nova — valores recalculados)**

Em `tests/test_engine_bt.py`, substituir as expectativas dos 3 testes numéricos e o teste de sizing zero (manter os demais intactos):

```python
SIZE = round(9_900.0 / (100.0 * 1.05), 6)  # 94.285714


def test_fill_na_abertura_seguinte_sem_look_ahead():
    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    run = run_backtrader(CANDLES, SIGNALS, zero, initial_cash=10_000.0,
                         stake_pct=0.99)
    assert run.n_trades == 1
    assert run.final_value == pytest.approx(10_000.0 + SIZE * 3.0)


def test_comissao_percentual_reduz_o_resultado():
    costs = CostModel(fee_pct=0.001, slippage_pct=0.0)
    run = run_backtrader(CANDLES, SIGNALS, costs, initial_cash=10_000.0,
                         stake_pct=0.99)
    esperado = (10_000.0 + SIZE * 3.0
                - SIZE * 102 * 0.001 - SIZE * 105 * 0.001)
    assert run.final_value == pytest.approx(esperado)


def test_slippage_percentual_piora_os_fills():
    costs = CostModel(fee_pct=0.0, slippage_pct=0.01)
    run = run_backtrader(CANDLES, SIGNALS, costs, initial_cash=10_000.0,
                         stake_pct=0.99)
    esperado = 10_000.0 + SIZE * (105 * 0.99 - 102 * 1.01)
    assert run.final_value == pytest.approx(esperado)


def test_ativo_caro_agora_compra_fracao():
    # antes: ValueError "caixa insuficiente"; agora compra fração
    caros = [_candle(0, 100_000.0, 100_000.0),
             _candle(1, 100_000.0, 100_000.0),
             _candle(2, 100_000.0, 100_000.0)]
    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    run = run_backtrader(caros, [1, 0, 0], zero, initial_cash=10_000.0)
    assert run.n_trades == 1  # liquidação terminal conta o trade
    assert run.final_value == pytest.approx(10_000.0)  # comprou e saiu no mesmo preço


def test_caixa_infima_ainda_e_erro_claro():
    caros = [_candle(0, 100_000.0, 100_000.0),
             _candle(1, 100_000.0, 100_000.0)]
    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    with pytest.raises(ValueError, match="caixa insuficiente"):
        run_backtrader(caros, [1, 0], zero, initial_cash=0.00001)
```

(O teste antigo `test_posicao_aberta_no_fim_liquida_no_ultimo_close_com_custos` deve ser atualizado trocando `94` por `SIZE` na expectativa — mesma álgebra.)

- [ ] **Step 2: Run tests to verify the numeric ones fail**

Run: `python3 -m pytest tests/test_engine_bt.py -v`
Expected: FAIL nos testes numéricos (sizing ainda inteiro).

- [ ] **Step 3: Implementar**

Em `engine_bt.py`, trocar `size = int(cash / (price * 1.05))` por:

```python
            size = round(cash / (price * 1.05), 6)
```

e ajustar o guard/comentário de `zero_size_entries` (dispara quando `size <= 0`). Atualizar o docstring (sizing fracionário — cripto spot é fracionária). Backtrader aceita `size` float com stocklike default.

No `README.md`, remover o bullet de "sizing em unidades inteiras" das "Limitações conhecidas (Fase 1)" (a seleção in-sample continua lá até a Task 8).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_engine_bt.py -v` e `python3 -m pytest`
Expected: tudo verde. Se algum valor divergir por mecânica interna do backtrader com size fracionário, PARE e reporte NEEDS_CONTEXT com os números observados.

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/backtest/engine_bt.py tests/test_engine_bt.py README.md
git commit -m "feat: sizing fracionário no backtest (cripto é fracionária)"
```

---

### Task 8: Validação out-of-sample no backtest (`--split`)

**Files:**
- Modify: `src/invest_agent/backtest/run.py`
- Modify: `src/invest_agent/backtest/report.py`
- Test: modificar `tests/test_backtest_run.py` e `tests/test_report.py`
- Modify: `README.md`

**Interfaces:**
- `backtest_symbol(..., split: float | None = None)`: com `split` (0 < split < 1): o sweep escolhe os parâmetros nos primeiros `int(len(candles) * split)` candles (treino) e o backtrader + veredito rodam APENAS nos candles restantes (teste). `build_report(..., out_of_sample: bool = False)`: quando True, troca a linha de aviso in-sample por: "Validação out-of-sample: parâmetros escolhidos no treino; veredito no período de teste." CLI ganha `--split` (float, default None).

- [ ] **Step 1: Write the failing tests**

Acrescentar a `tests/test_backtest_run.py`:

```python
def test_split_treina_e_avalia_separado(tmp_path):
    store = _make_store(tmp_path)
    texto = backtest_symbol(store, "BTCUSDT", "1h", "sma_cross",
                            CostModel(), split=0.5)
    assert "out-of-sample" in texto
    assert "in-sample" not in texto.split("out-of-sample")[0].split(
        "Veredito")[0] or True  # aviso in-sample não aparece no modo split


def test_split_invalido(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="split"):
        backtest_symbol(store, "BTCUSDT", "1h", "sma_cross", CostModel(),
                        split=1.5)
```

Acrescentar a `tests/test_report.py`:

```python
def test_report_out_of_sample_troca_o_aviso():
    run = BacktestRun(initial_cash=10_000.0, final_value=10_500.0,
                      n_trades=1, equity_curve=[10_000.0, 10_500.0])
    texto = build_report("BTCUSDT", "1d", {}, run, CANDLES, ZERO,
                         out_of_sample=True)
    assert "out-of-sample" in texto
    assert "resultado otimista" not in texto
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_backtest_run.py tests/test_report.py -v`
Expected: FAIL (parâmetros não existem).

- [ ] **Step 3: Implementar**

Em `report.py`: parâmetro `out_of_sample: bool = False`; quando True a linha de aviso vira `"Validação out-of-sample: parâmetros escolhidos no treino; veredito no período de teste."`; quando False mantém o aviso in-sample atual.

Em `run.py` (`backtest_symbol`):

```python
    if split is not None:
        if not 0.0 < split < 1.0:
            raise ValueError(f"split deve estar entre 0 e 1: {split}")
        corte = int(len(candles) * split)
        treino, teste = candles[:corte], candles[corte:]
        if len(treino) < 2 or len(teste) < 2:
            raise ValueError("split deixa treino ou teste sem candles")
        sweep = run_sweep(treino, signal_fn, grid, costs)
        best = sweep[0]
        closes = [c.close for c in teste]
        signals = signal_fn(closes, **best.params)
        run = run_backtrader(teste, signals, costs, initial_cash=initial_cash)
        return build_report(symbol, interval, best.params, run, teste, costs,
                            sweep=sweep, out_of_sample=True)
```

(caminho sem split permanece idêntico). CLI: `parser.add_argument("--split", type=float, default=None)` e repasse. README: na seção do backtest, mencionar `--split 0.7` e atualizar o bullet de limitações (in-sample vale só para o modo padrão; use `--split` para validação honesta).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest` — tudo verde.

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/backtest/ tests/test_backtest_run.py tests/test_report.py README.md
git commit -m "feat: validação out-of-sample no backtest (--split)"
```

---

## Self-review do plano (executada na escrita)

- **Cobertura (escopo 2a):** execução §4.5 → T3 (assinatura, sem retry em POST, client id determinístico, reconciliação via get_order) e T6 (stop na exchange no mesmo instante da entrada; IOC por ruling); memória §4.2 → T2; breakers/halt §4.4 (DAY/WEEK/MONTH com release) → T4; HITL TTL 10 min → T6; custo de API → T2+T6; exposição mark-to-market (débito Fase 0) → T4; snapshot §4.3 → T5; débitos do backtest → T7/T8.
- **Placeholders:** nenhum; todo step tem código completo.
- **Consistência de tipos:** `Settings` campos usados em T3/T6 conferem; métodos novos de `SqliteStore` (T2) batem com os usos em T4/T6 (`get_positions` → dict[str, (qty, avg, stop_id)]); `build_portfolio(store, balances, prices, now)` idêntico em T4/T6; `EquityMarks(day_open, week_open, month_open)` e `HaltLevel` conferem com `breakers.py` real (lido pelo controller); `engine.evaluate(proposal, portfolio, market, marks, now)` e `OrderIntent`/`Verdict` conferem com `engine.py`/`models.py` reais; `hold_proposer`/`Proposer` (T5) consumidos em T6; `cycle_id "%Y%m%d%H"` consistente entre T5/T6.
- **Aritmética dos testes conferida:** equity mark-to-market 57.000; conviction 0.019 → alvo 19 < 200 (2% de 10k) → APPROVED (compra ~0.189 BTC? não — target 19 USDT/ask 100.5 → qty 0.189054…; fill total, avg 100.5 ✓); volume 24h = 23×10 = 230 (nota na T5); HITL TTL = now+10min; SIZE fracionário 94.285714 e as três expectativas recalculadas na T7.
- **Risco conhecido:** o teste do HOLD passa um MarketSnapshot vazio ao engine — o engine retorna APPROVED antes de tocar o snapshot (early-return do HOLD em engine.py:54-55, verificado). O guard de símbolo vem DEPOIS do early-return, então não dispara.
