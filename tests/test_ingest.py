from datetime import datetime, timedelta, timezone

from invest_agent.data.ingest import ensure_history
from invest_agent.data.models import Candle
from invest_agent.data.store import CandleStore


def _candle(open_time: datetime, symbol: str = "BTCUSDT") -> Candle:
    return Candle(symbol=symbol, interval="1h", open_time=open_time,
                  open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0,
                  quote_volume=15.0, n_trades=3,
                  close_time=open_time + timedelta(minutes=59, seconds=59))


SINCE = datetime(2024, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2024, 3, 10, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, candles):
        self.candles = candles
        self.calls = []

    def klines_range(self, symbol, interval, start, end, limit=1000):
        self.calls.append((start, end))
        return [c for c in self.candles if start <= c.open_time < end]


def test_backfill_inicial_meses_mais_cauda_rest(tmp_path):
    store = CandleStore(tmp_path)
    month_calls = []

    def fake_fetch_month(symbol, interval, year, month, transport=None):
        month_calls.append((year, month))
        if (year, month) == (2024, 1):
            return None  # mês sem arquivo (ex.: símbolo listado depois)
        return [_candle(datetime(year, month, 1, tzinfo=timezone.utc))]

    tail = [_candle(datetime(2024, 3, 10, 10, 0, tzinfo=timezone.utc))]
    client = FakeClient(tail)
    added = ensure_history(store, client, "BTCUSDT", "1h", SINCE, NOW,
                           fetch_month_fn=fake_fetch_month)
    # meses até o ANTERIOR ao corrente: jan e fev; cauda REST cobre março
    assert month_calls == [(2024, 1), (2024, 2)]
    assert added == 2  # fev (jan era None) + 1 da cauda
    assert len(store.read("BTCUSDT", "1h")) == 2


def test_atualizacao_incremental_nao_baixa_meses(tmp_path):
    store = CandleStore(tmp_path)
    existing = _candle(datetime(2024, 3, 10, 9, 0, tzinfo=timezone.utc))
    store.append([existing])
    novo = _candle(datetime(2024, 3, 10, 10, 0, tzinfo=timezone.utc))
    client = FakeClient([existing, novo])

    def explode(*a, **k):
        raise AssertionError("não deve baixar meses quando o store já tem dados")

    added = ensure_history(store, client, "BTCUSDT", "1h", SINCE, NOW,
                           fetch_month_fn=explode)
    assert added == 1
    start_used = client.calls[0][0]
    assert start_used > existing.open_time  # não re-baixa o último candle


def test_idempotente(tmp_path):
    store = CandleStore(tmp_path)
    candles = [_candle(datetime(2024, 3, 10, h, 0, tzinfo=timezone.utc))
               for h in range(3)]
    client = FakeClient(candles)
    first = ensure_history(store, client, "BTCUSDT", "1h", SINCE, NOW,
                           fetch_month_fn=lambda *a, **k: None)
    second = ensure_history(store, client, "BTCUSDT", "1h", SINCE, NOW,
                            fetch_month_fn=lambda *a, **k: None)
    assert first == 3 and second == 0
