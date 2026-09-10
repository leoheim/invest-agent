from datetime import datetime, timedelta, timezone

import pytest

from invest_agent.backtest.costs import CostModel, trade_return
from invest_agent.backtest.sweep import SweepResult, run_sweep
from invest_agent.data.models import Candle

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candle(i: int, open_: float, close: float) -> Candle:
    return Candle(symbol="BTCUSDT", interval="1h",
                  open_time=T0 + timedelta(hours=i), open=open_,
                  high=max(open_, close) + 1, low=min(open_, close) - 1,
                  close=close, volume=1.0, quote_volume=1.0, n_trades=1,
                  close_time=T0 + timedelta(hours=i, minutes=59))


# sinal fixo para teste: ENTER no candle 1, EXIT no candle 3
def sinal_fixo(closes: list[float], **params) -> list[int]:
    out = [0] * len(closes)
    out[1], out[3] = 1, -1
    return out


CANDLES = [
    _candle(0, 100.0, 100.0),
    _candle(1, 100.0, 101.0),   # ENTER aqui → executa na abertura do 2
    _candle(2, 102.0, 108.0),
    _candle(3, 108.0, 109.0),   # EXIT aqui → executa na abertura do 4
    _candle(4, 110.0, 111.0),
]


def test_execucao_sem_look_ahead_na_abertura_seguinte():
    costs = CostModel(fee_pct=0.001, slippage_pct=0.0005)
    (res,) = run_sweep(CANDLES, sinal_fixo, [{}], costs)
    # entrada na ABERTURA do candle 2 (102), saída na ABERTURA do 4 (110)
    esperado = trade_return(102.0, 110.0, costs)
    assert res.total_return == pytest.approx(esperado)
    assert res.n_trades == 1


def test_posicao_aberta_no_fim_fecha_no_ultimo_close():
    def so_entra(closes, **p):
        out = [0] * len(closes)
        out[1] = 1
        return out

    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    (res,) = run_sweep(CANDLES, so_entra, [{}], zero)
    # entra na abertura do 2 (102), fecha forçado no close do último (111)
    assert res.total_return == pytest.approx(111.0 / 102.0 - 1)
    assert res.n_trades == 1


def test_ordena_por_retorno_e_carrega_params():
    def parametrizado(closes, ganho=1, **p):
        # ENTER no 0 → executa na abertura do 1
        out = [0] * len(closes)
        out[0] = 1
        return out

    subida = [_candle(0, 100.0, 100.0), _candle(1, 100.0, 100.0),
              _candle(2, 100.0, 150.0)]
    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    # duas combinações idênticas em sinal → mesmo retorno; garante que
    # params sobrevivem no resultado e a ordenação é estável/desc
    results = run_sweep(subida, parametrizado,
                        [{"ganho": 1}, {"ganho": 2}], zero)
    assert [r.params for r in results] == [{"ganho": 1}, {"ganho": 2}]
    assert all(r.total_return == pytest.approx(0.5) for r in results)


def test_sinal_no_ultimo_candle_e_ignorado():
    def entra_no_fim(closes, **p):
        out = [0] * len(closes)
        out[-1] = 1
        return out

    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    (res,) = run_sweep(CANDLES, entra_no_fim, [{}], zero)
    assert res.n_trades == 0 and res.total_return == 0.0
