"""Geradores de sinal mecânicos para o backtest — proxies determinísticos
do papel do LLM (sem LLM em backtest, spec §5). Sinal no índice i usa
apenas dados até o candle i; a execução acontece na abertura de i+1
(responsabilidade do executor, Tasks 3-4)."""
from __future__ import annotations

from enum import IntEnum

from ..indicators import rsi, sma


class Signal(IntEnum):
    ENTER = 1
    EXIT = -1
    HOLD = 0


def sma_cross_signals(closes: list[float], fast: int, slow: int) -> list[int]:
    """ENTER quando a média rápida cruza para CIMA da lenta; EXIT quando
    cruza para baixo. Mantém no máximo uma posição lógica aberta."""
    if fast >= slow:
        raise ValueError("fast deve ser menor que slow")
    fast_line, slow_line = sma(closes, fast), sma(closes, slow)
    out = [int(Signal.HOLD)] * len(closes)
    in_position = False
    for i in range(1, len(closes)):
        if None in (fast_line[i], slow_line[i],
                    fast_line[i - 1], slow_line[i - 1]):
            continue
        crossed_up = (fast_line[i - 1] <= slow_line[i - 1]
                      and fast_line[i] > slow_line[i])
        crossed_down = (fast_line[i - 1] >= slow_line[i - 1]
                        and fast_line[i] < slow_line[i])
        if crossed_up and not in_position:
            out[i] = int(Signal.ENTER)
            in_position = True
        elif crossed_down and in_position:
            out[i] = int(Signal.EXIT)
            in_position = False
    return out


def rsi_reversion_signals(closes: list[float], period: int = 14,
                          low: float = 30.0, high: float = 70.0) -> list[int]:
    """ENTER quando o RSI cruza de volta para cima do nível de sobrevenda
    (saindo dela); EXIT quando cruza para baixo do nível de sobrecompra."""
    line = rsi(closes, period)
    out = [int(Signal.HOLD)] * len(closes)
    in_position = False
    for i in range(1, len(closes)):
        if line[i] is None or line[i - 1] is None:
            continue
        left_oversold = line[i - 1] < low and line[i] >= low
        left_overbought = line[i - 1] > high and line[i] <= high
        if left_oversold and not in_position:
            out[i] = int(Signal.ENTER)
            in_position = True
        elif left_overbought and in_position:
            out[i] = int(Signal.EXIT)
            in_position = False
    return out


GRID_SMA: list[dict] = [
    {"fast": f, "slow": s}
    for f, s in [(10, 30), (10, 50), (20, 50), (20, 100), (50, 200)]
]

GRID_RSI: list[dict] = [
    {"period": p, "low": lo, "high": hi}
    for p in (7, 14, 21) for lo, hi in [(30.0, 70.0), (20.0, 80.0)]
]

STRATEGIES = {
    "sma_cross": (sma_cross_signals, GRID_SMA),
    "rsi_reversion": (rsi_reversion_signals, GRID_RSI),
}
