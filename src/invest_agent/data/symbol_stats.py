"""Coleta as estatísticas reais que alimentam build_whitelist (Fase 0,
spec §4.4): volume USDT de 30 dias e idade de listagem, via API pública.
Pensado para o job semanal da Fase 2: build_whitelist(fetch_symbol_stats(...))."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..whitelist import SymbolStats

_EPOCH = datetime(2017, 1, 1, tzinfo=timezone.utc)  # Binance abriu em 2017
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


def _is_leveraged(base: str) -> bool:
    return base.endswith(LEVERAGED_SUFFIXES)


def fetch_symbol_stats(client, now: datetime,
                       quote: str = "USDT") -> list[SymbolStats]:
    out: list[SymbolStats] = []
    for entry in client.exchange_info()["symbols"]:
        if entry["status"] != "TRADING" or entry["quoteAsset"] != quote:
            continue
        symbol, base = entry["symbol"], entry["baseAsset"]
        first = client.klines(symbol, "1d", start=_EPOCH, limit=1)
        if not first:
            continue
        listed_days = (now - first[0].open_time).days
        last30 = client.klines(symbol, "1d",
                               start=now - timedelta(days=31), end=now, limit=31)
        volume_30d = sum(c.quote_volume for c in last30)
        out.append(SymbolStats(symbol=symbol, base=base, quote=quote,
                               quote_volume_30d=volume_30d,
                               listed_days=listed_days,
                               is_leveraged=_is_leveraged(base)))
    return out
