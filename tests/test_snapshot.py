# tests/test_snapshot.py
from datetime import datetime, timedelta, timezone

from invest_agent.models import Action, PortfolioState, Position
from invest_agent.orchestrator.snapshot import (
    build_context, build_market_snapshot, context_hash, cycle_id,
    hold_proposer,
)
from invest_agent.data.models import Candle

NOW = datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc)


def _candle(hours_ago: int, close: float = 100.0) -> Candle:
    open_time = NOW.replace(minute=0) - timedelta(hours=hours_ago)
    return Candle(symbol="BTCUSDT", interval="1h", open_time=open_time,
                  open=close, high=close, low=close, close=close,
                  volume=1.0, quote_volume=10.0, n_trades=1,
                  close_time=open_time + timedelta(minutes=59, seconds=59))


def test_cycle_id_por_hora():
    assert cycle_id(NOW) == "2026091012"


def test_build_market_snapshot():
    candles = [_candle(h) for h in range(30, 0, -1)]
    snap = build_market_snapshot("BTCUSDT", 100.5, 100.0, 101.0,
                                 candles, NOW)
    assert snap.symbol == "BTCUSDT" and snap.last_price == 100.5
    assert snap.best_bid == 100.0 and snap.best_ask == 101.0
    # último candle: open 11:00, close 11:59:59 → idade ~30min
    assert 1800 <= snap.candle_age_seconds <= 1900
    # 24 candles nas últimas 24h (open_time >= 12:30 de ontem → 13h..11h = 23? conferir: aberturas 11:00,10:00,... >= 2026-09-09 12:30 → 11:00 de hoje até 13:00 de ontem = 23 candles) * 10.0
    assert snap.quote_volume_24h == 230.0


def test_build_market_snapshot_sem_candles():
    snap = build_market_snapshot("BTCUSDT", 1.0, 1.0, 1.0, [], NOW)
    assert snap.candle_age_seconds == float("inf")
    assert snap.quote_volume_24h == 0.0


def test_build_context_deterministico_e_hash():
    pf = PortfolioState(equity=10_000.0, cash=9_000.0,
                        positions={"BTCUSDT": Position("BTCUSDT", 0.01,
                                                       95_000.0)})
    candles = {"BTCUSDT": [_candle(h, 100.0 + h) for h in range(60, 0, -1)]}
    news = [("Bitcoin sobe", "coindesk", ("BTCUSDT",), "2026-09-10T10:00:00+00:00")]
    macro = {"fng": 34.0, "selic": 15.0}
    ctx1 = build_context(pf, frozenset({"BTCUSDT", "ETHUSDT"}), candles,
                         news, macro, NOW)
    ctx2 = build_context(pf, frozenset({"ETHUSDT", "BTCUSDT"}), candles,
                         news, macro, NOW)
    assert ctx1 == ctx2  # determinístico (whitelist ordenada)
    assert ctx1["cycle_id"] == "2026091012"
    assert ctx1["whitelist"] == ["BTCUSDT", "ETHUSDT"]
    assert ctx1["equity"] == 10_000.0
    assert ctx1["positions"]["BTCUSDT"]["qty"] == 0.01
    btc = ctx1["symbols"]["BTCUSDT"]
    assert btc["close"] == 101.0  # último candle: 100 + 1
    assert btc["rsi_14"] is not None and btc["sma_20"] is not None
    assert ctx1["news"][0]["title"] == "Bitcoin sobe"
    assert ctx1["macro"]["fng"] == 34.0
    assert context_hash(ctx1) == context_hash(ctx2)
    assert len(context_hash(ctx1)) == 64


def test_hold_proposer():
    ctx = {"cycle_id": "2026091012"}
    p = hold_proposer(ctx)
    assert p.action is Action.HOLD and p.conviction == 0.0
    assert p.cycle_id == "2026091012"
