"""SQLite (stdlib) — spec §4.2: notícias (com colunas de enriquecimento LLM
reservadas para a Fase 2), macro diário e o log de decisões APPEND-ONLY
(triggers abortam UPDATE/DELETE; é a base de auditoria, debug e IR)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ..news.canonical import simhash64
from ..news.models import NewsItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    ingested_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    simhash INTEGER NOT NULL,
    assets TEXT NOT NULL,
    summary_llm TEXT,
    sentiment TEXT,
    materiality INTEGER
);
CREATE TABLE IF NOT EXISTS macro (
    series TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (series, date)
);
CREATE TABLE IF NOT EXISTS decision_log (
    decision_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    inputs_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    verdict_json TEXT NOT NULL,
    order_json TEXT,
    fills_json TEXT,
    api_cost_usd REAL NOT NULL DEFAULT 0.0
);
CREATE TRIGGER IF NOT EXISTS decision_log_no_update
BEFORE UPDATE ON decision_log
BEGIN SELECT RAISE(ABORT, 'decision_log é append-only'); END;
CREATE TRIGGER IF NOT EXISTS decision_log_no_delete
BEFORE DELETE ON decision_log
BEGIN SELECT RAISE(ABORT, 'decision_log é append-only'); END;
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
CREATE INDEX IF NOT EXISTS idx_news_url ON news(url);
CREATE INDEX IF NOT EXISTS idx_news_simhash ON news(simhash);
CREATE INDEX IF NOT EXISTS idx_decision_ts ON decision_log(ts);
"""


@dataclass(frozen=True, slots=True)
class MacroPoint:
    series: str
    date: date
    value: float


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_id: str
    ts: datetime
    inputs_hash: str
    snapshot_json: str
    proposal_json: str
    verdict_json: str
    order_json: str | None = None
    fills_json: str | None = None
    api_cost_usd: float = 0.0


class SqliteStore:
    def __init__(self, path: Path):
        self._con = sqlite3.connect(path)
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    # --- notícias ---

    def insert_news(self,
                    items: list[tuple[NewsItem, tuple[str, ...]]]) -> int:
        added = 0
        for item, assets in items:
            cur = self._con.execute(
                "INSERT OR IGNORE INTO news (id, source, title, url,"
                " published_at, ingested_at, summary, simhash, assets)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (item.id, item.source, item.title, item.url,
                 item.published_at.isoformat() if item.published_at else None,
                 item.ingested_at.isoformat(), item.summary,
                 _to_signed(simhash64(item.title)), ",".join(assets)))
            added += cur.rowcount
        self._con.commit()
        return added

    def known_urls(self) -> set[str]:
        rows = self._con.execute("SELECT url FROM news").fetchall()
        return {url for (url,) in rows}

    def known_simhashes(self) -> list[int]:
        rows = self._con.execute("SELECT simhash FROM news").fetchall()
        return [_from_signed(value) for (value,) in rows]

    # --- enriquecimento LLM de notícias (Fase 2b) ---

    def unenriched_news(self, limit: int = 20) -> list[tuple[str, str, str]]:
        rows = self._con.execute(
            "SELECT id, title, summary FROM news WHERE summary_llm IS NULL"
            " ORDER BY ingested_at LIMIT ?", (limit,)).fetchall()
        return [tuple(r) for r in rows]

    def set_news_enrichment(self, news_id: str, summary_llm: str,
                            sentiment: str, materiality: int) -> None:
        self._con.execute(
            "UPDATE news SET summary_llm=?, sentiment=?, materiality=?"
            " WHERE id=?", (summary_llm, sentiment, materiality, news_id))
        self._con.commit()

    def recent_news(self, since: datetime, limit: int = 15) -> list[tuple]:
        rows = self._con.execute(
            "SELECT title, source, assets, published_at, sentiment,"
            " materiality FROM news WHERE ingested_at >= ?"
            " ORDER BY ingested_at DESC LIMIT ?",
            (since.isoformat(), limit)).fetchall()
        return [(r[0], r[1], tuple(a for a in r[2].split(",") if a),
                 r[3], r[4], r[5]) for r in rows]

    # --- macro ---

    def upsert_macro(self, point: MacroPoint) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO macro (series, date, value)"
            " VALUES (?,?,?)",
            (point.series, point.date.isoformat(), point.value))
        self._con.commit()

    def latest_macro(self, series: str) -> MacroPoint | None:
        row = self._con.execute(
            "SELECT series, date, value FROM macro WHERE series=?"
            " ORDER BY date DESC LIMIT 1", (series,)).fetchone()
        if row is None:
            return None
        return MacroPoint(row[0], date.fromisoformat(row[1]), row[2])

    # --- decision log (append-only) ---

    def append_decision(self, record: DecisionRecord) -> None:
        self._con.execute(
            "INSERT INTO decision_log (decision_id, ts, inputs_hash,"
            " snapshot_json, proposal_json, verdict_json, order_json,"
            " fills_json, api_cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
            (record.decision_id, record.ts.isoformat(), record.inputs_hash,
             record.snapshot_json, record.proposal_json, record.verdict_json,
             record.order_json, record.fills_json, record.api_cost_usd))
        self._con.commit()

    def read_decisions(self) -> list[DecisionRecord]:
        rows = self._con.execute(
            "SELECT decision_id, ts, inputs_hash, snapshot_json,"
            " proposal_json, verdict_json, order_json, fills_json,"
            " api_cost_usd FROM decision_log ORDER BY ts").fetchall()
        return [DecisionRecord(decision_id=r[0],
                               ts=datetime.fromisoformat(r[1]),
                               inputs_hash=r[2], snapshot_json=r[3],
                               proposal_json=r[4], verdict_json=r[5],
                               order_json=r[6], fills_json=r[7],
                               api_cost_usd=r[8])
                for r in rows]

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
        rows = self._con.execute(
            "SELECT ts, order_json FROM decision_log"
            " WHERE order_json IS NOT NULL ORDER BY ts").fetchall()
        out: dict[str, datetime] = {}
        for ts, order_json in rows:
            symbol = json.loads(order_json).get("symbol")
            if symbol:
                out[symbol] = datetime.fromisoformat(ts)
        return out


def _to_signed(value: int) -> int:
    """SQLite INTEGER é assinado de 64 bits; SimHash é sem sinal."""
    return value - (1 << 64) if value >= (1 << 63) else value


def _from_signed(value: int) -> int:
    return value + (1 << 64) if value < 0 else value
