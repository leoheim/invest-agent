"""Gates pré-ordem. Cada função retorna a lista de violações (em
português — os textos vão direto ao Telegram). Lista vazia = passou.
Nunca opere com dado velho; nunca negocie contra um book quebrado."""
from __future__ import annotations

from datetime import datetime, timedelta

from invest_agent.config import RiskProfile
from invest_agent.models import MarketSnapshot, PortfolioState


def check_market_quality(market: MarketSnapshot, profile: RiskProfile) -> list[str]:
    viol: list[str] = []
    if market.candle_age_seconds > profile.max_candle_age_seconds:
        viol.append(
            f"dado de mercado velho ({market.candle_age_seconds:.0f}s > "
            f"{profile.max_candle_age_seconds:.0f}s)")
    if not (0 < market.best_bid <= market.best_ask):
        viol.append("book inconsistente (bid/ask inválidos)")
    else:
        mid = (market.best_bid + market.best_ask) / 2
        spread = (market.best_ask - market.best_bid) / mid
        if spread > profile.max_spread_pct:
            viol.append(
                f"spread {spread:.2%} acima do máximo {profile.max_spread_pct:.2%}")
    if market.quote_volume_24h <= 0:
        viol.append("volume 24h zerado")
    return viol


def check_frequency(
    symbol: str,
    portfolio: PortfolioState,
    profile: RiskProfile,
    now: datetime,
) -> list[str]:
    viol: list[str] = []
    if portfolio.orders_today >= profile.max_orders_per_day:
        viol.append(
            f"máximo de ordens no dia atingido ({profile.max_orders_per_day})")
    last = portfolio.last_order_at.get(symbol)
    if last is not None:
        elapsed = now - last
        cooldown = timedelta(hours=profile.cooldown_hours)
        if elapsed < cooldown:
            restante = cooldown - elapsed
            viol.append(
                f"cooldown de {symbol} ativo (faltam {restante})")
    return viol
