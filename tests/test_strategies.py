import pytest

from invest_agent.backtest.strategies import (
    STRATEGIES, Signal, rsi_reversion_signals, sma_cross_signals,
)


def test_sma_cross_gera_entrada_e_saida():
    # fast=2, slow=3. Série sobe e depois despenca:
    closes = [10.0, 10.0, 10.0, 20.0, 30.0, 5.0, 4.0, 3.0]
    out = sma_cross_signals(closes, fast=2, slow=3)
    assert len(out) == len(closes)
    # até idx2 não há SMA(3) anterior definida → HOLD
    assert out[:3] == [0, 0, 0]
    # idx3: fast=(10+20)/2=15 > slow=(10+10+20)/3=13.33 e antes fast==slow → ENTER
    assert out[3] == Signal.ENTER
    # idx5: fast=(30+5)/2=17.5 < slow=(20+30+5)/3=18.33 e antes fast>slow → EXIT
    assert out[5] == Signal.EXIT
    # nunca dois ENTER seguidos sem EXIT no meio
    abertos = 0
    for s in out:
        if s == Signal.ENTER:
            abertos += 1
            assert abertos == 1
        elif s == Signal.EXIT:
            abertos -= 1


def test_sma_cross_fast_deve_ser_menor_que_slow():
    with pytest.raises(ValueError):
        sma_cross_signals([1.0, 2.0], fast=3, slow=2)


def test_rsi_reversion_sai_da_sobrevenda_gera_entrada():
    # queda longa (RSI→0, entra em sobrevenda) seguida de recuperação
    closes = [float(x) for x in range(30, 10, -1)] + [15.0, 20.0, 25.0]
    out = rsi_reversion_signals(closes, period=14, low=30.0, high=70.0)
    assert len(out) == len(closes)
    assert Signal.ENTER in out  # cruzou de <30 para >=30 na recuperação
    idx_enter = out.index(Signal.ENTER)
    assert idx_enter >= 20  # só depois da virada


def test_rsi_reversion_sem_cruzamento_nao_sinaliza():
    closes = [10.0, 10.0, 10.0, 10.0]
    assert rsi_reversion_signals(closes, period=14) == [0, 0, 0, 0]


def test_registry_tem_as_duas_familias_com_grades():
    assert set(STRATEGIES) == {"sma_cross", "rsi_reversion"}
    fn, grid = STRATEGIES["sma_cross"]
    assert fn is sma_cross_signals and len(grid) >= 4
    for params in grid:
        assert params["fast"] < params["slow"]
