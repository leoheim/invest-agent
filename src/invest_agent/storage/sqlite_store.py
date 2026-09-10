"""SQLite (stdlib) — spec §4.2: notícias (com colunas de enriquecimento LLM
reservadas para a Fase 2), macro diário e o log de decisões APPEND-ONLY
(triggers abortam UPDATE/DELETE; é a base de auditoria, debug e IR)."""
from __future__ import annotations

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


def _to_signed(value: int) -> int:
    """SQLite INTEGER é assinado de 64 bits; SimHash é sem sinal."""
    return value - (1 << 64) if value >= (1 << 63) else value


def _from_signed(value: int) -> int:
    return value + (1 << 64) if value < 0 else value
