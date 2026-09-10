"""Backfill + atualização incremental de candles, idempotente: primeiro
carregamento via zips mensais do data.binance.vision, cauda e updates via
REST. O main() do CLI é a ÚNICA borda do projeto que lê o relógio e usa a
rede reais — toda a lógica recebe now/transport injetados."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .binance_client import BinanceMarketData
from .history import fetch_month, month_range
from .store import CandleStore


def ensure_history(store: CandleStore, client, symbol: str, interval: str,
                   since: datetime, now: datetime,
                   fetch_month_fn=fetch_month, transport=None) -> int:
    """Garante candles de `since` até `now` no store. Retorna o nº de
    candles novos. Store vazio: backfill mensal até o mês ANTERIOR ao de
    `now` + cauda REST. Store com dados: só a cauda REST a partir do último
    candle (os zips mensais não são re-baixados)."""
    added = 0
    if store.latest_open_time(symbol, interval) is None:
        last_full_month_end = date(now.year, now.month, 1) - timedelta(days=1)
        for year, month in month_range(since.date(), last_full_month_end):
            candles = fetch_month_fn(symbol, interval, year, month,
                                     transport=transport)
            if candles:
                added += store.append(candles)
    latest = store.latest_open_time(symbol, interval)
    start = latest + timedelta(milliseconds=1) if latest is not None else since
    tail = client.klines_range(symbol, interval, start=start, end=now)
    if tail:
        tail = [c for c in tail if c.close_time <= now]
        added += store.append(tail)
    return added


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Backfill e atualização de candles da Binance")
    parser.add_argument("--symbol", required=True, help="ex.: BTCUSDT")
    parser.add_argument("--interval", default="1h", help="ex.: 1h, 4h, 1d")
    parser.add_argument("--since", default="2024-01-01",
                        help="data inicial UTC (YYYY-MM-DD)")
    parser.add_argument("--root", default=Path("data/candles"), type=Path)
    args = parser.parse_args(argv)

    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)  # borda de composição: único lugar com relógio real
    store = CandleStore(args.root)
    client = BinanceMarketData()
    added = ensure_history(store, client, args.symbol, args.interval, since, now)
    print(f"{args.symbol} {args.interval}: {added} candles novos em {args.root}")


if __name__ == "__main__":
    main()
