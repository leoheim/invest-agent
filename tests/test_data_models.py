from datetime import datetime, timezone

from invest_agent.data.models import Candle

# Linha real de kline REST (ms): open_time, open, high, low, close, volume,
# close_time, quote_volume, n_trades, taker_base, taker_quote, ignore
ROW_MS = [1704067200000, "42283.58", "42554.57", "42261.02", "42475.23",
          "1271.68", 1704070799999, "53950211.04", 47134, "600.0", "25000000.0", "0"]

# Mesma linha como viria de um CSV 2025+ do data.binance.vision (µs)
ROW_US = [1704067200000000, "42283.58", "42554.57", "42261.02", "42475.23",
          "1271.68", 1704070799999999, "53950211.04", 47134, "600.0", "25000000.0", "0"]


def test_from_kline_row_ms():
    c = Candle.from_kline_row("BTCUSDT", "1h", ROW_MS)
    assert c.symbol == "BTCUSDT" and c.interval == "1h"
    assert c.open_time == datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert c.open == 42283.58 and c.close == 42475.23
    assert c.quote_volume == 53950211.04 and c.n_trades == 47134
    assert c.close_time == datetime(2024, 1, 1, 0, 59, 59, 999000, tzinfo=timezone.utc)


def test_from_kline_row_microssegundos_normaliza():
    ms = Candle.from_kline_row("BTCUSDT", "1h", ROW_MS)
    us = Candle.from_kline_row("BTCUSDT", "1h", ROW_US)
    assert us.open_time == ms.open_time


def test_candle_e_imutavel():
    c = Candle.from_kline_row("BTCUSDT", "1h", ROW_MS)
    try:
        c.close = 0.0
        assert False, "devia ser frozen"
    except AttributeError:
        pass
