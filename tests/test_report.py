from datetime import datetime, timedelta, timezone

from invest_agent.backtest.costs import CostModel, buy_and_hold_return
from invest_agent.backtest.engine_bt import BacktestRun
from invest_agent.backtest.report import build_report
from invest_agent.backtest.sweep import SweepResult
from invest_agent.data.models import Candle

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candle(i: int, open_: float, close: float) -> Candle:
    return Candle(symbol="BTCUSDT", interval="1d",
                  open_time=T0 + timedelta(days=i), open=open_,
                  high=max(open_, close), low=min(open_, close),
                  close=close, volume=1.0, quote_volume=1.0, n_trades=1,
                  close_time=T0 + timedelta(days=i, hours=23))


CANDLES = [_candle(0, 100.0, 105.0), _candle(1, 105.0, 110.0)]
ZERO = CostModel(fee_pct=0.0, slippage_pct=0.0)


def test_report_estrategia_que_supera():
    run = BacktestRun(initial_cash=10_000.0, final_value=12_000.0,
                      n_trades=3, equity_curve=[10_000.0, 11_000.0, 12_000.0])
    texto = build_report("BTCUSDT", "1d", {"fast": 10, "slow": 30}, run,
                         CANDLES, ZERO)
    assert "BTCUSDT" in texto and "1d" in texto
    assert "+20.00%" in texto           # retorno da estratégia
    assert "+10.00%" in texto           # buy-and-hold: 100 → 110 sem custos
    assert "SUPERA o buy-and-hold" in texto
    assert "3" in texto                 # nº de trades
    assert "fast" in texto              # params visíveis


def test_report_estrategia_que_nao_supera():
    run = BacktestRun(initial_cash=10_000.0, final_value=10_100.0,
                      n_trades=1, equity_curve=[10_000.0, 10_100.0])
    texto = build_report("BTCUSDT", "1d", {}, run, CANDLES, ZERO)
    assert "NÃO SUPERA o buy-and-hold" in texto


def test_report_inclui_drawdown_e_top_sweep():
    run = BacktestRun(initial_cash=10_000.0, final_value=10_500.0,
                      n_trades=2, equity_curve=[10_000.0, 12_000.0, 9_000.0,
                                                10_500.0])
    sweep = [SweepResult(params={"fast": 10, "slow": 30},
                         total_return=0.30, n_trades=4),
             SweepResult(params={"fast": 20, "slow": 50},
                         total_return=0.10, n_trades=2)]
    texto = build_report("BTCUSDT", "1d", {"fast": 10, "slow": 30}, run,
                         CANDLES, ZERO, sweep=sweep)
    assert "25.00%" in texto            # drawdown (12000→9000)
    assert "+30.00%" in texto and "+10.00%" in texto  # linhas do sweep
