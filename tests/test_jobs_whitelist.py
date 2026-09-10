from datetime import datetime, timedelta, timezone

from invest_agent.data.models import Candle
from invest_agent.jobs.whitelist import refresh_whitelist
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _daily(symbol, days_ago, quote_volume):
    open_time = NOW - timedelta(days=days_ago)
    return Candle(symbol=symbol, interval="1d", open_time=open_time,
                  open=1.0, high=2.0, low=0.5, close=1.5, volume=1.0,
                  quote_volume=quote_volume, n_trades=1,
                  close_time=open_time + timedelta(days=1))


class FakeClient:
    def exchange_info(self):
        return {"symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC",
             "quoteAsset": "USDT"},
            {"symbol": "SOLUSDT", "status": "TRADING", "baseAsset": "SOL",
             "quoteAsset": "USDT"},
        ]}

    def klines(self, symbol, interval, start=None, end=None, limit=1000):
        if limit == 1:
            return [_daily(symbol, 3000, 1.0)]
        return [_daily(symbol, d, 100.0) for d in range(1, 31)]


def test_refresh_whitelist_persiste(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    out = refresh_whitelist(FakeClient(), store, NOW)
    assert {"BTCUSDT", "ETHUSDT", "SOLUSDT"} <= out  # ETH via ALWAYS_INCLUDED
    assert store.get_whitelist() == out
    store.close()
