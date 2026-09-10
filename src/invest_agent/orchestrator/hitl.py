# src/invest_agent/orchestrator/hitl.py
"""HITL obrigatório da spec §4.4 que exige HISTÓRICO (por isso vive no
orquestrador, não no engine puro): primeiro trade em ativo novo e
primeira ordem após um circuit breaker. Só endurece vereditos
(APPROVED → NEEDS_APPROVAL) — nunca afrouxa."""
from __future__ import annotations

from datetime import datetime

from ..models import (Action, PortfolioState, Proposal, Verdict,
                      VerdictStatus)
from ..storage.sqlite_store import SqliteStore


def apply_hitl_overrides(verdict: Verdict, proposal: Proposal,
                         portfolio: PortfolioState, store: SqliteStore,
                         now: datetime) -> Verdict:
    if verdict.status is not VerdictStatus.APPROVED or verdict.order is None:
        return verdict

    reasons: list[str] = []
    last_orders = store.last_order_at_by_symbol()

    if (proposal.action is Action.BUY
            and proposal.symbol not in portfolio.positions
            and proposal.symbol not in last_orders):
        reasons.append(f"primeiro trade em {proposal.symbol} — "
                       "aprovação humana obrigatória")

    halt = store.get_halt()
    if halt is not None:
        level_name, set_at, _ = halt
        last_global = max(last_orders.values(), default=None)
        if last_global is None or last_global < set_at:
            reasons.append(f"primeira ordem após circuit breaker "
                           f"(nível {level_name}) — aprovação humana "
                           "obrigatória")

    if not reasons:
        return verdict
    return Verdict(VerdictStatus.NEEDS_APPROVAL, reasons, verdict.order)
