# src/invest_agent/orchestrator/cycle.py
"""Um ciclo do agente (spec §4.3): heartbeat → expira HITL vencido →
halt/custo → carteira mark-to-market → marks → proposta (Proposer
injetável; LLM só na Fase 2b) → RulesEngine → decision_log SEMPRE →
execução (LIMIT IOC + stop na exchange). O main() é a borda única com
env/relógio/rede reais."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from ..breakers import HaltLevel, check_breakers
from ..config import MODERADO
from ..data.store import CandleStore
from ..engine import RulesEngine
from ..killswitch import KillSwitch, heartbeat_beat
from ..models import Action, OrderIntent, Proposal, Verdict, VerdictStatus
from ..settings import Settings
from ..storage.sqlite_store import DecisionRecord, SqliteStore
from ..whitelist import ALWAYS_INCLUDED
from .snapshot import (Proposer, build_context, build_market_snapshot,
                       context_hash, cycle_id, hold_proposer)
from .state import (active_halt, build_portfolio, ensure_marks,
                    record_halt_if_needed)

HITL_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class CycleResult:
    cycle_id: str
    verdict_status: str
    reasons: list[str]
    executed: bool
    halted: str | None = None


def _expire_pending(store: SqliteStore, now: datetime) -> None:
    for decision_id, _, expires_at, _ in store.get_pending():
        if expires_at < now:
            store.set_pending_status(decision_id, "expired")


def _record(store: SqliteStore, decision_id: str, now: datetime,
            inputs_hash: str, context: dict, proposal: Proposal,
            verdict: Verdict) -> None:
    store.append_decision(DecisionRecord(
        decision_id=decision_id,
        ts=now,
        inputs_hash=inputs_hash,
        snapshot_json=json.dumps(context, sort_keys=True,
                                 ensure_ascii=False),
        proposal_json=json.dumps({
            "symbol": proposal.symbol, "action": proposal.action.value,
            "conviction": proposal.conviction,
            "rationale": proposal.rationale,
            "cycle_id": proposal.cycle_id}, ensure_ascii=False),
        verdict_json=json.dumps({
            "status": verdict.status.value,
            "reasons": verdict.reasons}, ensure_ascii=False),
        order_json=(json.dumps(asdict(verdict.order), ensure_ascii=False)
                    if verdict.order else None),
    ))


def _execute(store: SqliteStore, adapter, order: OrderIntent) -> bool:
    positions = store.get_positions()
    old = positions.get(order.symbol)
    if order.side == "SELL" and old and old[2]:
        try:
            adapter.cancel_order(order.symbol, old[2])
        except Exception:
            pass  # stop pode já ter executado/expirado — reconciliação natural
    resp = adapter.place_limit_ioc(order)
    executed_qty = float(resp.get("executedQty", "0") or 0)
    if executed_qty <= 0:
        return False
    quote = float(resp.get("cummulativeQuoteQty", "0") or 0)
    fill_price = quote / executed_qty if executed_qty else order.limit_price
    if order.side == "BUY":
        old_qty, old_avg = (old[0], old[1]) if old else (0.0, 0.0)
        new_qty = old_qty + executed_qty
        new_avg = ((old_qty * old_avg) + (executed_qty * fill_price)) / new_qty
        stop_id = f"{order.client_order_id}-sl"
        adapter.place_stop_loss(order.symbol, executed_qty,
                                order.stop_loss_price, stop_id)
        store.upsert_position(order.symbol, new_qty, new_avg, stop_id)
    else:
        remaining = (old[0] if old else 0.0) - executed_qty
        if remaining <= 1e-9:
            store.delete_position(order.symbol)
        else:
            store.upsert_position(order.symbol, remaining,
                                  old[1] if old else fill_price, None)
    return True


def run_cycle(store: SqliteStore, candle_store: CandleStore, adapter,
              engine: RulesEngine, proposer: Proposer, settings: Settings,
              now: datetime, dry_run: bool = False) -> CycleResult:
    cid = cycle_id(now)
    heartbeat_beat(settings.heartbeat_path, now)
    _expire_pending(store, now)

    halt = active_halt(store, now)
    if halt is not HaltLevel.NONE:
        return CycleResult(cid, "halted",
                           [f"halt {halt.name} ativo"], False, halt.name)

    if store.api_cost_today(now.date()) >= settings.api_cost_daily_cap_usd:
        record_halt_if_needed(store, HaltLevel.DAY, now)
        return CycleResult(cid, "halted",
                           ["custo de API diário acima do teto"], False,
                           HaltLevel.DAY.name)

    balances = adapter.get_balances()
    known = store.get_positions()
    prices = {symbol: adapter.get_price(symbol) for symbol in known}
    portfolio = build_portfolio(store, balances, prices, now)
    marks = ensure_marks(store, portfolio.equity, now)

    whitelist = frozenset(engine.whitelist)
    candles_by_symbol = {
        s: candle_store.read(s, "1h", start=now - timedelta(days=7))
        for s in sorted(whitelist)}
    context = build_context(portfolio, whitelist, candles_by_symbol,
                            news=[], macro={}, now=now)
    inputs_hash = context_hash(context)

    proposal = proposer(context)
    decision_id = f"{cid}-{proposal.symbol}"

    if proposal.action is Action.HOLD:
        verdict = engine.evaluate(
            proposal, portfolio,
            build_market_snapshot(proposal.symbol, 0.0, 0.0, 0.0, [], now),
            marks, now)
        _record(store, decision_id, now, inputs_hash, context, proposal,
                verdict)
        return CycleResult(cid, verdict.status.value, verdict.reasons, False)

    bid, ask = adapter.get_book(proposal.symbol)
    last = adapter.get_price(proposal.symbol)
    market = build_market_snapshot(
        proposal.symbol, last, bid, ask,
        candles_by_symbol.get(proposal.symbol, []), now)
    verdict = engine.evaluate(proposal, portfolio, market, marks, now)
    record_halt_if_needed(
        store, check_breakers(portfolio.equity, marks, engine.profile), now)
    _record(store, decision_id, now, inputs_hash, context, proposal, verdict)

    if verdict.status is VerdictStatus.NEEDS_APPROVAL:
        store.add_pending(decision_id, now, now + HITL_TTL)
        return CycleResult(cid, verdict.status.value, verdict.reasons, False)
    if verdict.status is not VerdictStatus.APPROVED or verdict.order is None:
        return CycleResult(cid, verdict.status.value, verdict.reasons, False)
    if dry_run:
        return CycleResult(cid, verdict.status.value,
                           ["dry-run: ordem não enviada"], False)
    executed = _execute(store, adapter, verdict.order)
    return CycleResult(cid, verdict.status.value, verdict.reasons, executed)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Um ciclo do agente")
    parser.add_argument("--dry-run", action="store_true",
                        help="avalia e registra, mas não envia ordens")
    args = parser.parse_args(argv)

    from ..execution.binance_adapter import BinanceSpotAdapter

    settings = Settings.from_env(os.environ)  # borda única
    now = datetime.now(timezone.utc)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(settings.db_path)
    candle_store = CandleStore(settings.candles_root)
    adapter = BinanceSpotAdapter(settings.binance_api_key,
                                 settings.binance_api_secret,
                                 settings.binance_base_url)
    engine = RulesEngine(MODERADO, ALWAYS_INCLUDED,
                         KillSwitch(settings.kill_switch_path))
    result = run_cycle(store, candle_store, adapter, engine, hold_proposer,
                       settings, now, dry_run=args.dry_run)
    print(f"ciclo {result.cycle_id}: {result.verdict_status}"
          + (f" ({'; '.join(result.reasons)})" if result.reasons else "")
          + (" — ordem executada" if result.executed else ""))
    store.close()


if __name__ == "__main__":
    main()
