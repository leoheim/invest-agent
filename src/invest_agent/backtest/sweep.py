"""Varredura de parâmetros em Python puro — o filtro grosseiro que a spec
delegava ao vectorbt (substituído por ruling: llvmlite não compila nesta
máquina; papel idêntico). Execução simulada sem look-ahead: sinal no
candle i executa na ABERTURA do candle i+1. O resultado alimenta o estágio
de fills realistas (backtrader, Task 4)."""
from __future__ import annotations

from dataclasses import dataclass

from ..data.models import Candle
from .costs import CostModel, trade_return
from .strategies import Signal


@dataclass(frozen=True)
class SweepResult:
    params: dict
    total_return: float
    n_trades: int


def _simulate(candles: list[Candle], signals: list[int],
              costs: CostModel) -> tuple[float, int]:
    equity = 1.0
    n_trades = 0
    entry_price: float | None = None
    for i in range(len(candles) - 1):  # sinal no último candle não executa
        next_open = candles[i + 1].open
        if signals[i] == Signal.ENTER and entry_price is None:
            entry_price = next_open
        elif signals[i] == Signal.EXIT and entry_price is not None:
            equity *= 1 + trade_return(entry_price, next_open, costs)
            entry_price = None
            n_trades += 1
    if entry_price is not None:  # fecha posição pendente no último close
        equity *= 1 + trade_return(entry_price, candles[-1].close, costs)
        n_trades += 1
    return equity - 1, n_trades


def run_sweep(candles: list[Candle], signal_fn, grid: list[dict],
              costs: CostModel) -> list[SweepResult]:
    closes = [c.close for c in candles]
    results = []
    for params in grid:
        signals = signal_fn(closes, **params)
        total, n_trades = _simulate(candles, signals, costs)
        results.append(SweepResult(params=params, total_return=total,
                                   n_trades=n_trades))
    return sorted(results, key=lambda r: r.total_return, reverse=True)
