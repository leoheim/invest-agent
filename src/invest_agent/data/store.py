"""Armazenamento local de candles (spec §4.2): Parquet particionado por
símbolo/intervalo, um arquivo por mês, consultado via DuckDB. Escrita
atômica (tmp + rename); dedupe por open_time (o mais recente vence)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .models import Candle

_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("interval", pa.string()),
    ("open_time", pa.timestamp("us", tz="UTC")),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.float64()),
    ("quote_volume", pa.float64()),
    ("n_trades", pa.int64()),
    ("close_time", pa.timestamp("us", tz="UTC")),
])

_COLUMNS = [f.name for f in _SCHEMA]


def _to_table(rows: list[Candle]) -> pa.Table:
    return pa.table(
        {name: [getattr(c, name) for c in rows] for name in _COLUMNS},
        schema=_SCHEMA,
    )


def _read_file(path: Path) -> list[Candle]:
    return [Candle(**row) for row in pq.read_table(path).to_pylist()]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    return con


class CandleStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, symbol: str, interval: str) -> Path:
        return self.root / symbol / interval

    def append(self, candles: list[Candle]) -> int:
        by_file: dict[Path, list[Candle]] = {}
        for c in candles:
            path = self._dir(c.symbol, c.interval) / f"{c.open_time:%Y-%m}.parquet"
            by_file.setdefault(path, []).append(c)
        added = 0
        for path, batch in by_file.items():
            merged: dict[datetime, Candle] = {}
            if path.exists():
                for row in _read_file(path):
                    merged[row.open_time] = row
            before = len(merged)
            for c in batch:
                merged[c.open_time] = c
            added += len(merged) - before
            rows = sorted(merged.values(), key=lambda c: c.open_time)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            pq.write_table(_to_table(rows), tmp)
            tmp.replace(path)
        return added

    def read(self, symbol: str, interval: str,
             start: datetime | None = None,
             end: datetime | None = None) -> list[Candle]:
        directory = self._dir(symbol, interval)
        if not any(directory.glob("*.parquet")):
            return []
        query = "SELECT * FROM read_parquet(?) WHERE 1=1"
        args: list = [str(directory / "*.parquet")]
        if start is not None:
            query += " AND open_time >= ?"
            args.append(start)
        if end is not None:
            query += " AND open_time < ?"
            args.append(end)
        query += " ORDER BY open_time"
        con = _connect()
        try:
            rows = con.execute(query, args).fetchall()
        finally:
            con.close()
        return [
            Candle(symbol=r[0], interval=r[1], open_time=_ensure_utc(r[2]),
                   open=r[3], high=r[4], low=r[5], close=r[6], volume=r[7],
                   quote_volume=r[8], n_trades=int(r[9]),
                   close_time=_ensure_utc(r[10]))
            for r in rows
        ]

    def latest_open_time(self, symbol: str, interval: str) -> datetime | None:
        directory = self._dir(symbol, interval)
        if not any(directory.glob("*.parquet")):
            return None
        con = _connect()
        try:
            (ts,) = con.execute(
                "SELECT max(open_time) FROM read_parquet(?)",
                [str(directory / "*.parquet")],
            ).fetchone()
        finally:
            con.close()
        return _ensure_utc(ts) if ts is not None else None
