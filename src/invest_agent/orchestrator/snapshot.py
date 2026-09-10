# src/invest_agent/orchestrator/snapshot.py
"""Snapshot do ciclo (spec §4.3 passo 1): código monta TODO o contexto —
candles+indicadores calculados em Python, notícias já enriquecidas,
posições, macro — e o hash dos inputs vai para o decision_log (auditoria:
que dados exatos gearam a decisão). O LLM (Fase 2b) recebe este dict
pronto; um Proposer é qualquer Callable[[dict], Proposal]."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Callable

from ..data.models import Candle
from ..indicators import rsi, sma
from ..models import Action, MarketSnapshot, PortfolioState, Proposal

Proposer = Callable[[dict], Proposal]


def cycle_id(now: datetime) -> str:
    return f"{now:%Y%m%d%H}"


def build_market_snapshot(symbol: str, last_price: float, bid: float,
                          ask: float, candles: list[Candle],
                          now: datetime) -> MarketSnapshot:
    if candles:
        age = (now - candles[-1].close_time).total_seconds()
    else:
        age = float("inf")
    cutoff = now - timedelta(hours=24)
    volume_24h = sum(c.quote_volume for c in candles
                     if c.open_time >= cutoff)
    return MarketSnapshot(symbol=symbol, last_price=last_price,
                          best_bid=bid, best_ask=ask,
                          candle_age_seconds=age,
                          quote_volume_24h=volume_24h)


def _symbol_features(candles: list[Candle]) -> dict:
    closes = [c.close for c in candles]
    rsi_line = rsi(closes, 14)
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    return {
        "close": closes[-1] if closes else None,
        "rsi_14": rsi_line[-1] if closes else None,
        "sma_20": sma20[-1] if closes else None,
        "sma_50": sma50[-1] if closes else None,
    }


def build_context(portfolio: PortfolioState, whitelist: frozenset[str],
                  candles_by_symbol: dict[str, list[Candle]],
                  news: list[tuple], macro: dict[str, float],
                  now: datetime) -> dict:
    return {
        "cycle_id": cycle_id(now),
        "now": now.isoformat(),
        "whitelist": sorted(whitelist),
        "equity": portfolio.equity,
        "cash": portfolio.cash,
        "positions": {
            symbol: {"qty": p.qty, "avg_price": p.avg_price}
            for symbol, p in sorted(portfolio.positions.items())
        },
        "symbols": {
            symbol: _symbol_features(candles)
            for symbol, candles in sorted(candles_by_symbol.items())
        },
        "news": [
            {"title": n[0], "source": n[1], "assets": sorted(n[2]),
             "published_at": n[3],
             **({"sentiment": n[4], "materiality": n[5]} if len(n) > 4 else {})}
            for n in news
        ],
        "macro": dict(sorted(macro.items())),
    }


def context_hash(context: dict) -> str:
    payload = json.dumps(context, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def hold_proposer(context: dict) -> Proposal:
    """Proposer da Fase 2a: nunca opera. O ciclo inteiro roda (snapshot,
    veredito, log) sem nenhum LLM — dry-run operacional."""
    return Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale="dry-run sem LLM", cycle_id=context["cycle_id"])
