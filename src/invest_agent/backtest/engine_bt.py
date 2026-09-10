"""Estágio de fills realistas (spec §5): backtrader executa as ordens a
mercado na ABERTURA do candle seguinte ao sinal — sem look-ahead e sem
cheat-on-close. Comissão e slippage percentuais vêm do CostModel."""
from __future__ import annotations

from dataclasses import dataclass

import backtrader as bt

from ..data.models import Candle
from .costs import CostModel
from .strategies import Signal


@dataclass(frozen=True)
class BacktestRun:
    initial_cash: float
    final_value: float
    n_trades: int
    equity_curve: list[float]


class _CandleData(bt.feeds.DataBase):
    """Feed que serve diretamente a lista de Candle (sem CSV/pandas).
    Usa o sistema de params do backtrader (idioma canônico p/ feeds)."""

    params = (("candles", None),)

    def start(self):
        super().start()
        self._idx = 0

    def _load(self) -> bool:
        if self._idx >= len(self.p.candles):
            return False
        c = self.p.candles[self._idx]
        self._idx += 1
        self.lines.datetime[0] = bt.date2num(c.open_time.replace(tzinfo=None))
        self.lines.open[0] = c.open
        self.lines.high[0] = c.high
        self.lines.low[0] = c.low
        self.lines.close[0] = c.close
        self.lines.volume[0] = c.volume
        self.lines.openinterest[0] = 0.0
        return True


class _SignalStrategy(bt.Strategy):
    params = (("signals", None), ("stake_pct", 0.99))

    def __init__(self):
        self._bar = 0
        self.closed_trades = 0
        self.equity_curve: list[float] = []

    def next(self):
        self.equity_curve.append(self.broker.getvalue())
        signal = self.p.signals[self._bar]
        self._bar += 1
        if signal == Signal.ENTER and not self.position:
            cash = self.broker.getcash() * self.p.stake_pct
            price = self.data.close[0]
            size = int(cash / (price * 1.05))  # margem p/ gap+slippage
            if size > 0:
                self.buy(size=size)
        elif signal == Signal.EXIT and self.position:
            self.close()

    def notify_trade(self, trade):
        if trade.isclosed:
            self.closed_trades += 1


def run_backtrader(candles: list[Candle], signals: list[int],
                   costs: CostModel, initial_cash: float = 10_000.0,
                   stake_pct: float = 0.99) -> BacktestRun:
    if len(candles) != len(signals):
        raise ValueError("candles e signals devem ter o mesmo comprimento")
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=costs.fee_pct)
    if costs.slippage_pct:
        cerebro.broker.set_slippage_perc(
            perc=costs.slippage_pct, slip_open=True, slip_match=True,
            slip_out=True)
    cerebro.adddata(_CandleData(candles=candles))
    cerebro.addstrategy(_SignalStrategy, signals=signals,
                        stake_pct=stake_pct)
    (strat,) = cerebro.run()
    return BacktestRun(initial_cash=initial_cash,
                       final_value=cerebro.broker.getvalue(),
                       n_trades=strat.closed_trades,
                       equity_curve=strat.equity_curve)
