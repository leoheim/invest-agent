from datetime import datetime, timedelta, timezone

from invest_agent.data.models import Candle
from invest_agent.data.symbol_stats import fetch_symbol_stats
from invest_agent.whitelist import build_whitelist

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _daily(symbol: str, days_ago: int, quote_volume: float) -> Candle:
    open_time = NOW - timedelta(days=days_ago)
    return Candle(symbol=symbol, interval="1d", open_time=open_time,
                  open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0,
                  quote_volume=quote_volume, n_trades=3,
                  close_time=open_time + timedelta(days=1))


class FakeClient:
    def __init__(self):
        self.info = {"symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING",
             "baseAsset": "BTC", "quoteAsset": "USDT"},
            {"symbol": "NEWUSDT", "status": "TRADING",
             "baseAsset": "NEW", "quoteAsset": "USDT"},
            {"symbol": "ETHUPUSDT", "status": "TRADING",
             "baseAsset": "ETHUP", "quoteAsset": "USDT"},
            {"symbol": "XYZBRL", "status": "TRADING",
             "baseAsset": "XYZ", "quoteAsset": "BRL"},
            {"symbol": "OLDUSDT", "status": "BREAK",
             "baseAsset": "OLD", "quoteAsset": "USDT"},
        ]}
        # primeiro kline de cada símbolo (idade) e últimos 30 diários (volume)
        self.first = {
            "BTCUSDT": _daily("BTCUSDT", 3000, 1.0),
            "NEWUSDT": _daily("NEWUSDT", 90, 1.0),
            "ETHUPUSDT": _daily("ETHUPUSDT", 900, 1.0),
        }
        self.month_volume = {"BTCUSDT": 500.0, "NEWUSDT": 900.0, "ETHUPUSDT": 100.0}

    def exchange_info(self):
        return self.info

    def klines(self, symbol, interval, start=None, end=None, limit=1000):
        assert interval == "1d"
        if limit == 1:
            return [self.first[symbol]]
        return [_daily(symbol, d, self.month_volume[symbol]) for d in range(1, 31)]


def test_fetch_symbol_stats_filtra_e_calcula():
    stats = {s.symbol: s for s in fetch_symbol_stats(FakeClient(), NOW)}
    assert set(stats) == {"BTCUSDT", "NEWUSDT", "ETHUPUSDT"}  # BRL e BREAK fora
    assert stats["BTCUSDT"].listed_days == 3000
    assert stats["BTCUSDT"].quote_volume_30d == 500.0 * 30
    assert stats["ETHUPUSDT"].is_leveraged is True
    assert stats["NEWUSDT"].is_leveraged is False
    assert stats["BTCUSDT"].quote == "USDT"


def test_saida_alimenta_build_whitelist():
    wl = build_whitelist(fetch_symbol_stats(FakeClient(), NOW), size=20)
    # NEWUSDT tem 90 dias (< 365) e ETHUPUSDT é alavancado: só BTC entra
    # pelo critério; ETHUSDT entra por ALWAYS_INCLUDED.
    assert wl == frozenset({"BTCUSDT", "ETHUSDT"})
