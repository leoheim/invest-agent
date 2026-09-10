from datetime import datetime, timedelta, timezone

import pytest

from invest_agent.backtest.costs import CostModel
from invest_agent.backtest.engine_bt import run_backtrader
from invest_agent.data.models import Candle

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candle(i: int, open_: float, close: float) -> Candle:
    return Candle(symbol="BTCUSDT", interval="1d",
                  open_time=T0 + timedelta(days=i), open=open_,
                  high=max(open_, close) + 1, low=min(open_, close) - 1,
                  close=close, volume=1.0, quote_volume=1.0, n_trades=1,
                  close_time=T0 + timedelta(days=i, hours=23))


CANDLES = [
    _candle(0, 100.0, 100.0),
    _candle(1, 102.0, 104.0),   # fill da entrada acontece AQUI (abertura 102)
    _candle(2, 106.0, 108.0),
    _candle(3, 105.0, 107.0),   # fill da saída AQUI (abertura 105)
    _candle(4, 107.0, 109.0),
]
# ENTER no candle 0, EXIT no candle 2:
SIGNALS = [1, 0, -1, 0, 0]


def test_fill_na_abertura_seguinte_sem_look_ahead():
    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    run = run_backtrader(CANDLES, SIGNALS, zero, initial_cash=10_000.0,
                         stake_pct=0.99)
    # sizing usa o CLOSE do candle do sinal (100) com margem de 5%:
    # size = int(10000*0.99 / (100*1.05)) = 94 unidades
    # compra fill na abertura do candle 1 (102); venda na abertura do 3 (105)
    # lucro = 94 * (105 - 102) = 282
    assert run.n_trades == 1
    assert run.final_value == pytest.approx(10_000.0 + 94 * 3.0)


def test_comissao_percentual_reduz_o_resultado():
    costs = CostModel(fee_pct=0.001, slippage_pct=0.0)
    run = run_backtrader(CANDLES, SIGNALS, costs, initial_cash=10_000.0,
                         stake_pct=0.99)
    # mesmas 94 unidades; comissões: 94*102*0.001 + 94*105*0.001
    esperado = 10_000.0 + 94 * 3.0 - 94 * 102 * 0.001 - 94 * 105 * 0.001
    assert run.final_value == pytest.approx(esperado)


def test_slippage_percentual_piora_os_fills():
    costs = CostModel(fee_pct=0.0, slippage_pct=0.01)
    run = run_backtrader(CANDLES, SIGNALS, costs, initial_cash=10_000.0,
                         stake_pct=0.99)
    # sizing não muda (usa o close do sinal): 94 unidades
    # compra fill = 102*1.01 = 103.02; venda fill = 105*0.99 = 103.95
    esperado = 10_000.0 + 94 * (105 * 0.99 - 102 * 1.01)
    assert run.final_value == pytest.approx(esperado)


def test_equity_curve_tem_um_ponto_por_candle():
    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    run = run_backtrader(CANDLES, SIGNALS, zero)
    assert len(run.equity_curve) == len(CANDLES)
    assert run.equity_curve[0] == pytest.approx(10_000.0)


def test_posicao_aberta_no_fim_liquida_no_ultimo_close_com_custos():
    # ENTER no candle 0, sem EXIT: a posição fica aberta até o fim dos dados
    costs = CostModel(fee_pct=0.001, slippage_pct=0.0)
    run = run_backtrader(CANDLES, [1, 0, 0, 0, 0], costs,
                         initial_cash=10_000.0, stake_pct=0.99)
    # mesmas 94 unidades; compra fill 102 (com comissão); sem EXIT, a
    # posição é liquidada no ÚLTIMO close (109) com custo de saída —
    # espelha o fechamento forçado do sweep (Task 3)
    esperado = (10_000.0 - 94 * 102 * 0.001 + 94 * (109 - 102)
                - 94 * 109 * 0.001)
    assert run.final_value == pytest.approx(esperado)
    assert run.n_trades == 1
