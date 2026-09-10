import pytest

from invest_agent.indicators import atr, ema, rsi, sma


def test_sma_janela_2():
    assert sma([1.0, 2.0, 3.0, 4.0], 2) == [None, 1.5, 2.5, 3.5]


def test_sma_periodo_invalido():
    with pytest.raises(ValueError):
        sma([1.0], 0)


def test_ema_seed_e_suavizacao():
    # period=3: seed = SMA(1,2,3) = 2.0 no índice 2; k = 0.5
    # idx3 = 4*0.5 + 2*0.5 = 3.0 ; idx4 = 5*0.5 + 3*0.5 = 4.0
    assert ema([1.0, 2.0, 3.0, 4.0, 5.0], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_serie_curta_e_toda_none():
    assert ema([1.0, 2.0], 3) == [None, None]


def test_rsi_alta_continua_e_100():
    valores = [float(i) for i in range(1, 20)]
    out = rsi(valores, 14)
    assert out[14] == 100.0 and out[-1] == 100.0


def test_rsi_queda_continua_e_0():
    valores = [float(i) for i in range(20, 1, -1)]
    out = rsi(valores, 14)
    assert out[14] == 0.0 and out[-1] == 0.0


def test_rsi_ganhos_e_perdas_iguais_da_50():
    out = rsi([1.0, 2.0, 1.0], 2)
    assert out == [None, None, 50.0]


def test_atr_wilder_calculado_a_mao():
    # TR: [2, max(3,3,0)=3, max(1,0,1)=1]; period=2:
    # atr[1] = (2+3)/2 = 2.5 ; atr[2] = (2.5*1 + 1)/2 = 1.75
    highs, lows, closes = [10.0, 12.0, 11.0], [8.0, 9.0, 10.0], [9.0, 11.0, 10.5]
    assert atr(highs, lows, closes, 2) == [None, 2.5, 1.75]


def test_atr_series_de_tamanhos_diferentes():
    with pytest.raises(ValueError):
        atr([1.0], [1.0, 2.0], [1.0], 2)
