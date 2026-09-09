"""Whitelist dinâmica: o agente escolhe livremente DENTRO desta lista;
ticker fora dela é rejeitado pelo motor. Recalculada por código (job
semanal na Fase 2) — o LLM nunca influencia os critérios."""
from __future__ import annotations

from dataclasses import dataclass

ALWAYS_INCLUDED: frozenset[str] = frozenset({"BTCUSDT", "ETHUSDT"})

STABLECOIN_BASES: frozenset[str] = frozenset({
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "BUSD", "USDP", "PYUSD", "EURI",
})

MIN_LISTED_DAYS = 365


@dataclass(frozen=True)
class SymbolStats:
    symbol: str
    base: str
    quote: str
    quote_volume_30d: float
    listed_days: int
    is_leveraged: bool


def build_whitelist(stats: list[SymbolStats], size: int = 20) -> frozenset[str]:
    eligible = [
        s for s in stats
        if s.quote == "USDT"
        and s.base not in STABLECOIN_BASES
        and not s.is_leveraged
        and s.listed_days >= MIN_LISTED_DAYS
    ]
    top = sorted(eligible, key=lambda s: s.quote_volume_30d, reverse=True)[:size]
    return ALWAYS_INCLUDED | {s.symbol for s in top}
