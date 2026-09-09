"""Circuit breakers de drawdown. O orquestrador (Fase 2) persiste os
marks e converte o HaltLevel em ação: DAY = pausa até D+1, WEEK = pausa
+ alerta, MONTH = desliga e exige retomada manual pelo dono."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from invest_agent.config import RiskProfile


class HaltLevel(Enum):
    NONE = 0
    DAY = 1
    WEEK = 2
    MONTH = 3


@dataclass(frozen=True)
class EquityMarks:
    day_open: float
    week_open: float
    month_open: float


def _drawdown(open_value: float, now_value: float) -> float:
    if open_value <= 0:
        return 0.0
    return max(0.0, (open_value - now_value) / open_value)


def check_breakers(
    equity_now: float,
    marks: EquityMarks,
    profile: RiskProfile,
) -> HaltLevel:
    if _drawdown(marks.month_open, equity_now) >= profile.monthly_loss_halt_pct:
        return HaltLevel.MONTH
    if _drawdown(marks.week_open, equity_now) >= profile.weekly_loss_halt_pct:
        return HaltLevel.WEEK
    if _drawdown(marks.day_open, equity_now) >= profile.daily_loss_halt_pct:
        return HaltLevel.DAY
    return HaltLevel.NONE
