from dataclasses import replace
from datetime import datetime, timedelta, timezone

from invest_agent.data.models import Candle
from invest_agent.data.store import CandleStore


def _candle(hours: int, close: float = 1.5, symbol: str = "BTCUSDT") -> Candle:
    open_time = datetime(2024, 1, 31, 22, 0, tzinfo=timezone.utc) + timedelta(hours=hours)
    return Candle(symbol=symbol, interval="1h", open_time=open_time,
                  open=1.0, high=2.0, low=0.5, close=close, volume=10.0,
                  quote_volume=15.0, n_trades=3,
                  close_time=open_time + timedelta(minutes=59, seconds=59))


def test_roundtrip_append_read(tmp_path):
    store = CandleStore(tmp_path)
    candles = [_candle(i) for i in range(4)]
    assert store.append(candles) == 4
    out = store.read("BTCUSDT", "1h")
    assert out == sorted(candles, key=lambda c: c.open_time)


def test_particiona_por_mes(tmp_path):
    store = CandleStore(tmp_path)
    store.append([_candle(0), _candle(3)])  # 31/jan 22h e 01/fev 01h UTC
    files = sorted(p.name for p in (tmp_path / "BTCUSDT" / "1h").glob("*.parquet"))
    assert files == ["2024-01.parquet", "2024-02.parquet"]


def test_dedupe_por_open_time_o_mais_novo_vence(tmp_path):
    store = CandleStore(tmp_path)
    original = _candle(0)
    assert store.append([original]) == 1
    corrigido = replace(original, close=9.9)
    assert store.append([corrigido]) == 0  # mesmo open_time: zero novos
    out = store.read("BTCUSDT", "1h")
    assert len(out) == 1 and out[0].close == 9.9


def test_read_filtra_start_inclusivo_end_exclusivo(tmp_path):
    store = CandleStore(tmp_path)
    candles = [_candle(i) for i in range(4)]
    store.append(candles)
    out = store.read("BTCUSDT", "1h",
                     start=candles[1].open_time, end=candles[3].open_time)
    assert out == candles[1:3]


def test_latest_open_time(tmp_path):
    store = CandleStore(tmp_path)
    assert store.latest_open_time("BTCUSDT", "1h") is None
    candles = [_candle(i) for i in range(3)]
    store.append(candles)
    assert store.latest_open_time("BTCUSDT", "1h") == candles[-1].open_time


def test_read_de_simbolo_vazio(tmp_path):
    assert CandleStore(tmp_path).read("ETHUSDT", "1h") == []
