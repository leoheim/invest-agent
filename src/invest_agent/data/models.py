"""Modelo de candle e a ÚNICA normalização de timestamps da Binance:
REST usa milissegundos; arquivos do data.binance.vision de 2025+ usam
microssegundos. Detectamos pelo valor (>= 10^14 => µs)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

_MICROS_THRESHOLD = 100_000_000_000_000  # 10^14


def _to_utc(ts: int | float | str) -> datetime:
    ts = int(ts)
    if ts >= _MICROS_THRESHOLD:
        ts //= 1000  # µs -> ms
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    interval: str
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float          # volume na moeda base
    quote_volume: float    # volume na moeda de cotação (USDT)
    n_trades: int
    close_time: datetime

    @classmethod
    def from_kline_row(cls, symbol: str, interval: str, row: list) -> "Candle":
        """Linha de kline (REST /api/v3/klines ou CSV do data.binance.vision).
        As 12 colunas têm a mesma ordem nos dois formatos; usamos as 9 primeiras."""
        return cls(
            symbol=symbol,
            interval=interval,
            open_time=_to_utc(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            close_time=_to_utc(row[6]),
            quote_volume=float(row[7]),
            n_trades=int(row[8]),
        )
