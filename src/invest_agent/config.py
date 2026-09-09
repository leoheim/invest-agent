"""Perfis de risco. Valores do perfil moderado travados na spec
(docs/superpowers/specs/2026-09-05-invest-agent-design.md, seção 2).
SÓ um humano edita este arquivo — nenhum caminho de código escreve aqui."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskProfile:
    name: str
    max_position_pct: float
    max_exposure_pct: float
    stop_loss_pct: float
    max_orders_per_day: int
    cooldown_hours: float
    hitl_threshold_pct: float
    daily_loss_halt_pct: float
    weekly_loss_halt_pct: float
    monthly_loss_halt_pct: float
    max_spread_pct: float
    max_candle_age_seconds: float
    min_notional_usdt: float


MODERADO = RiskProfile(
    name="moderado",
    max_position_pct=0.10,
    max_exposure_pct=0.60,
    stop_loss_pct=0.05,
    max_orders_per_day=4,
    cooldown_hours=4.0,
    hitl_threshold_pct=0.02,
    daily_loss_halt_pct=0.05,
    weekly_loss_halt_pct=0.10,
    monthly_loss_halt_pct=0.15,
    max_spread_pct=0.005,
    max_candle_age_seconds=600.0,
    min_notional_usdt=10.0,
)
