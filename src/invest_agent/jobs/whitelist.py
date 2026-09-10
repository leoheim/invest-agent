"""Job semanal (spec §4.4): recalcula a whitelist por critérios e
persiste no SQLite — o ciclo passa a usá-la automaticamente."""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from ..data.symbol_stats import fetch_symbol_stats
from ..storage.sqlite_store import SqliteStore
from ..whitelist import build_whitelist


def refresh_whitelist(client, store: SqliteStore,
                      now: datetime) -> frozenset[str]:
    stats = fetch_symbol_stats(client, now)
    symbols = build_whitelist(stats)
    store.set_whitelist(sorted(symbols), now)
    return symbols


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Recalcula a whitelist")
    parser.add_argument("--db", default=Path("data/agent.db"), type=Path)
    args = parser.parse_args(argv)

    from ..data.binance_client import BinanceMarketData
    from ..settings import Settings

    settings = Settings.from_env(os.environ)
    client = BinanceMarketData(base_url=settings.binance_base_url)
    store = SqliteStore(args.db)
    now = datetime.now(timezone.utc)  # borda
    symbols = refresh_whitelist(client, store, now)
    store.close()
    print(f"whitelist atualizada ({len(symbols)}): "
          + ", ".join(sorted(symbols)))


if __name__ == "__main__":
    main()
