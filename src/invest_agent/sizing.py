"""Sizing é 100% código. A convicção do LLM apenas MODULA o tamanho
dentro do teto — nunca o define. Aritmética de posição em LLM é fonte
garantida de erro (ver pesquisa, relatório de arquitetura §3.3)."""
from __future__ import annotations

from invest_agent.config import RiskProfile


def compute_buy_qty(
    equity: float,
    price: float,
    conviction: float,
    current_position_notional: float,
    total_invested_notional: float,
    profile: RiskProfile,
) -> float:
    if price <= 0 or equity <= 0:
        return 0.0

    target = equity * profile.max_position_pct * conviction
    room_asset = equity * profile.max_position_pct - current_position_notional
    room_total = equity * profile.max_exposure_pct - total_invested_notional
    notional = min(target, room_asset, room_total)

    if notional < profile.min_notional_usdt:
        return 0.0
    return notional / price
