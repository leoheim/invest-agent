from datetime import datetime, timedelta, timezone

import pytest

from invest_agent.backtest.costs import CostModel
from invest_agent.backtest.run import backtest_symbol
from invest_agent.data.models import Candle
from invest_agent.data.store import CandleStore

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candle(i: int, close: float) -> Candle:
    open_ = close  # candles "chatos": open == close, sem ruído
    return Candle(symbol="BTCUSDT", interval="1h",
                  open_time=T0 + timedelta(hours=i), open=open_,
                  high=close + 0.5, low=close - 0.5, close=close,
                  volume=1.0, quote_volume=1.0, n_trades=1,
                  close_time=T0 + timedelta(hours=i, minutes=59))


def _make_store(tmp_path) -> CandleStore:
    store = CandleStore(tmp_path)
    # 250 candles: sobe, cai, sobe — o suficiente para cruzamentos de SMA
    closes = ([100.0 + i for i in range(100)]           # 100→199
              + [199.0 - 2 * i for i in range(50)]      # 199→101
              + [101.0 + i for i in range(100)])        # 101→200
    store.append([_candle(i, c) for i, c in enumerate(closes)])
    return store


def test_backtest_symbol_produz_relatorio_completo(tmp_path):
    store = _make_store(tmp_path)
    texto = backtest_symbol(store, "BTCUSDT", "1h", "sma_cross",
                            CostModel())
    assert "Backtest BTCUSDT 1h" in texto
    assert "buy-and-hold" in texto
    assert "Veredito:" in texto
    assert "Top do sweep" in texto


def test_backtest_symbol_estrategia_desconhecida(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="estratégia desconhecida"):
        backtest_symbol(store, "BTCUSDT", "1h", "nao_existe", CostModel())


def test_backtest_symbol_sem_candles(tmp_path):
    store = CandleStore(tmp_path)
    with pytest.raises(ValueError, match="sem candles"):
        backtest_symbol(store, "BTCUSDT", "1h", "sma_cross", CostModel())


def test_split_treina_e_avalia_separado(tmp_path):
    store = _make_store(tmp_path)
    texto = backtest_symbol(store, "BTCUSDT", "1h", "sma_cross",
                            CostModel(), split=0.5)
    assert "out-of-sample" in texto
    assert "resultado otimista" not in texto  # aviso in-sample não aparece no modo split


def test_split_invalido(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="split"):
        backtest_symbol(store, "BTCUSDT", "1h", "sma_cross", CostModel(),
                        split=1.5)
