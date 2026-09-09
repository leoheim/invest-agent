"""Modelos de domínio. Frozen dataclasses: o estado nunca é mutado em
lugar nenhum — cada ciclo constrói snapshots novos."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"
    HOLD = "hold"


@dataclass(frozen=True)
class Proposal:
    """O que o LLM produz. Note o que NÃO está aqui: preço e quantidade —
    sizing é responsabilidade exclusiva do código."""
    symbol: str
    action: Action
    conviction: float
    rationale: str
    cycle_id: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.conviction <= 1.0:
            raise ValueError(f"conviction fora de 0..1: {self.conviction}")


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_price: float

    def notional(self, price: float) -> float:
        return self.qty * price


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    last_price: float
    best_bid: float
    best_ask: float
    candle_age_seconds: float
    quote_volume_24h: float


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    orders_today: int = 0
    last_order_at: dict[str, datetime] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str  # "BUY" | "SELL"
    qty: float
    limit_price: float
    stop_loss_price: float | None
    client_order_id: str


class VerdictStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_APPROVAL = "needs_approval"


@dataclass(frozen=True)
class Verdict:
    status: VerdictStatus
    reasons: list[str]
    order: OrderIntent | None
