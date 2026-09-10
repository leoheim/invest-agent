"""Indicadores em Python puro (spec §4.3: "candles+indicadores calculados
em Python"). Entradas em ordem cronológica; a saída tem o MESMO comprimento
da entrada, com None nas posições sem valor definido. RSI e ATR usam a
suavização de Wilder."""
from __future__ import annotations


def _check_period(period: int) -> None:
    if period <= 0:
        raise ValueError("period deve ser positivo")


def sma(values: list[float], period: int) -> list[float | None]:
    _check_period(period)
    out: list[float | None] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= period:
            acc -= values[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    _check_period(period)
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period  # seed = SMA do primeiro período
    out[period - 1] = prev
    k = 2 / (period + 1)
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    _check_period(period)
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain, avg_loss = gains / period, losses / period

    def _rsi(g: float, p: float) -> float:
        if p == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / p)

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def atr(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> list[float | None]:
    _check_period(period)
    n = len(closes)
    if not (len(highs) == len(lows) == n):
        raise ValueError("séries de tamanhos diferentes")
    out: list[float | None] = [None] * n
    if n == 0:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    if n < period:
        return out
    prev = sum(trs[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out
