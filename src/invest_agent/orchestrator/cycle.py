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
from typing import Callable

from ..breakers import HaltLevel, check_breakers
from ..config import MODERADO
from ..data.store import CandleStore
from ..engine import RulesEngine
from ..killswitch import KillSwitch, heartbeat_beat
from ..models import Action, OrderIntent, Proposal, Verdict, VerdictStatus
from ..settings import Settings
from ..storage.sqlite_store import DecisionRecord, SqliteStore
from ..whitelist import ALWAYS_INCLUDED
from .hitl import apply_hitl_overrides
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


def llm_pre_gates(store: SqliteStore, settings: Settings, now: datetime) -> bool:
    """Espelha EXATAMENTE os gates de halt/custo que run_cycle já checa —
    defesa na borda de composição (I1): sem isso, o enriquecimento de
    notícias em main() gastaria API mesmo com o breaker de custo ou um
    halt já ativos, invertendo a semântica fail-closed do breaker."""
    if active_halt(store, now) is not HaltLevel.NONE:
        return False
    if store.api_cost_today(now.date()) >= settings.api_cost_daily_cap_usd:
        return False
    return True


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


def _execute(store: SqliteStore, adapter, order: OrderIntent,
             stop_loss_pct: float) -> float:
    """Retorna a quantidade de fato executada (0.0 = IOC sem fill)."""
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
        return 0.0
    quote = float(resp.get("cummulativeQuoteQty", "0") or 0)
    fill_price = quote / executed_qty if executed_qty else order.limit_price
    if order.side == "BUY":
        old_qty, old_avg, old_stop_id = old if old else (0.0, 0.0, None)
        new_qty = old_qty + executed_qty
        new_avg = ((old_qty * old_avg) + (executed_qty * fill_price)) / new_qty
        # I1: a posição fica visível ANTES do stop — uma falha no
        # place_stop_loss não pode deixar moedas sem posição rastreada.
        store.upsert_position(order.symbol, new_qty, new_avg, None)
        if old_stop_id:
            # I2: um stop só por posição — cancela o antigo (que travava
            # só old_qty) antes de colocar um novo cobrindo new_qty,
            # senão ele fica orfão (resting, id perdido, qty travada).
            try:
                adapter.cancel_order(order.symbol, old_stop_id)
            except Exception:
                pass  # stop pode já ter executado/expirado
        stop_id = f"{order.client_order_id}-sl"
        adapter.place_stop_loss(order.symbol, new_qty,
                                order.stop_loss_price, stop_id)
        store.upsert_position(order.symbol, new_qty, new_avg, stop_id)
    else:
        remaining = (old[0] if old else 0.0) - executed_qty
        if remaining <= 1e-9:
            store.delete_position(order.symbol)
        else:
            # fill parcial de IOC: o remanescente fica descoberto até
            # recolocarmos o stop na exchange (ruling do controller).
            avg = old[1] if old else fill_price
            # I1: persiste o remanescente ANTES do stop, mesma razão.
            store.upsert_position(order.symbol, remaining, avg, None)
            stop_price = order.limit_price * (1 - stop_loss_pct)
            stop_id = f"{order.client_order_id}-sl"
            adapter.place_stop_loss(order.symbol, remaining, stop_price,
                                    stop_id)
            store.upsert_position(order.symbol, remaining, avg, stop_id)
    return executed_qty


def _record_fills(store: SqliteStore, decision_id: str, executed_qty: float,
                  now: datetime) -> None:
    store.set_decision_fills(decision_id, json.dumps(
        {"executed_qty": executed_qty, "ts": now.isoformat()},
        ensure_ascii=False))


def _notify(notifier: Callable[[str], None] | None, text: str) -> None:
    if notifier is None:
        return
    try:
        notifier(text)
    except Exception:
        pass  # notificação nunca derruba o ciclo


def _execute_approved(store: SqliteStore, adapter,
                      notifier: Callable[[str], None] | None,
                      dry_run: bool, stop_loss_pct: float,
                      now: datetime) -> None:
    if dry_run:
        return
    for decision_id in store.approved_pending():
        if not store.claim_pending(decision_id):
            # I2: outra execução (outro processo, ou um ciclo manual
            # sobreposto ao cron) já reivindicou esta linha — não reenvia.
            continue
        record = store.get_decision(decision_id)
        if record is None or not record.order_json:
            store.set_pending_status(decision_id, "executed")
            continue
        if now - record.ts > timedelta(hours=24):
            # aprovação nunca reavaliada por 24h+ — não executa às cegas,
            # exige re-aprovação sobre condições atuais.
            store.set_pending_status(decision_id, "expired")
            _notify(notifier,
                    f"⚠️ aprovação {decision_id} expirada (mais de 24h) — "
                    f"re-aprove se ainda quiser executar")
            continue
        order = OrderIntent(**json.loads(record.order_json))
        try:
            executed_qty = _execute(store, adapter, order, stop_loss_pct)
        except Exception as err:
            # CRITICAL: sem isto, a linha ficava 'approved' para sempre e o
            # próximo ciclo reenviaria a MESMA ordem (mesmo
            # client_order_id) de novo — double-buy sem stop, repetido a
            # cada ciclo. Marca 'failed' (fora do alcance de
            # approved_pending) e re-levanta: fail-closed preservado, mas
            # sem retry automático.
            store.set_pending_status(decision_id, "failed")
            _notify(notifier,
                    f"⚠️ ordem aprovada {decision_id} falhou: "
                    f"{type(err).__name__} — não será re-tentada")
            raise
        if executed_qty > 0:
            # C2: só marca a decisão como EXECUTADA (fills_json) quando
            # de fato houve fill — é isto que o HITL obrigatório passa a
            # consultar para não desarmar em cima de mera intenção.
            _record_fills(store, decision_id, executed_qty, now)
        store.set_pending_status(decision_id, "executed")
        _notify(notifier,
                f"✅ ordem aprovada executada: {order.side} {order.qty:g} "
                f"{order.symbol}" if executed_qty > 0 else
                f"⚠️ ordem aprovada {decision_id} não executou (IOC sem fill)")


def run_cycle(store: SqliteStore, candle_store: CandleStore, adapter,
              engine: RulesEngine, proposer: Proposer, settings: Settings,
              now: datetime, dry_run: bool = False,
              news: list[tuple] = (), macro: dict[str, float] | None = None,
              notifier: Callable[[str], None] | None = None,
              hitl_notifier: Callable[[str, OrderIntent, list[str]], None]
              | None = None) -> CycleResult:
    cid = cycle_id(now)
    heartbeat_beat(settings.heartbeat_path, now)
    _expire_pending(store, now)

    halt = active_halt(store, now)
    if halt is not HaltLevel.NONE:
        # I3: aprovadas esperam a liberação do halt — não são enviadas
        # enquanto um breaker está ativo (a linha permanece 'approved').
        return CycleResult(cid, "halted",
                           [f"halt {halt.name} ativo"], False, halt.name)

    # C1: kill switch (/kill, /pausar, dead-man) tem que barrar até uma
    # ordem JÁ APROVADA — sem este gate, ele só era consultado dentro de
    # engine.evaluate (propostas novas), deixando o dono sem freio nenhum
    # sobre uma aprovação pendente de execução. A linha fica 'approved',
    # como no gate de halt; o resto do ciclo segue normal (engine.evaluate
    # já rejeita propostas novas com o kill switch ativo).
    if not engine.kill_switch.is_active():
        _execute_approved(store, adapter, notifier, dry_run,
                          engine.profile.stop_loss_pct, now)

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
    record_halt_if_needed(
        store, check_breakers(portfolio.equity, marks, engine.profile), now)

    whitelist = frozenset(engine.whitelist)
    candles_by_symbol = {
        s: candle_store.read(s, "1h", start=now - timedelta(days=7))
        for s in sorted(whitelist)}
    context = build_context(portfolio, whitelist, candles_by_symbol,
                            news=list(news), macro=dict(macro or {}), now=now)
    inputs_hash = context_hash(context)

    proposal = proposer(context)
    decision_id = f"{cid}-{proposal.symbol}"

    if proposal.action is Action.HOLD:
        verdict = engine.evaluate(
            proposal, portfolio,
            build_market_snapshot(proposal.symbol, 0.0, 0.0, 0.0, [], now),
            marks, now)
        verdict = apply_hitl_overrides(verdict, proposal, portfolio, store,
                                       now)
        _record(store, decision_id, now, inputs_hash, context, proposal,
                verdict)
        return CycleResult(cid, verdict.status.value, verdict.reasons, False)

    bid, ask = adapter.get_book(proposal.symbol)
    last = adapter.get_price(proposal.symbol)
    market = build_market_snapshot(
        proposal.symbol, last, bid, ask,
        candles_by_symbol.get(proposal.symbol, []), now)
    verdict = engine.evaluate(proposal, portfolio, market, marks, now)
    verdict = apply_hitl_overrides(verdict, proposal, portfolio, store, now)
    _record(store, decision_id, now, inputs_hash, context, proposal, verdict)

    if verdict.status is VerdictStatus.NEEDS_APPROVAL:
        store.add_pending(decision_id, now, now + HITL_TTL)
        if hitl_notifier is not None:
            try:
                hitl_notifier(decision_id, verdict.order, verdict.reasons)
            except Exception:
                pass  # notificação nunca derruba o ciclo
        return CycleResult(cid, verdict.status.value, verdict.reasons, False)
    if verdict.status is not VerdictStatus.APPROVED or verdict.order is None:
        return CycleResult(cid, verdict.status.value, verdict.reasons, False)
    if dry_run:
        return CycleResult(cid, verdict.status.value,
                           ["dry-run: ordem não enviada"], False)
    executed_qty = _execute(store, adapter, verdict.order,
                            engine.profile.stop_loss_pct)
    if executed_qty > 0:
        # C2: marca a decisão como EXECUTADA — ver comentário equivalente
        # em _execute_approved.
        _record_fills(store, decision_id, executed_qty, now)
    return CycleResult(cid, verdict.status.value, verdict.reasons,
                       executed_qty > 0)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Um ciclo do agente")
    parser.add_argument("--dry-run", action="store_true",
                        help="avalia e registra, mas não envia ordens")
    parser.add_argument("--llm", action="store_true",
                        help="usa o cérebro Claude (triagem + proposta)")
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
    whitelist = store.get_whitelist() or ALWAYS_INCLUDED
    engine = RulesEngine(MODERADO, whitelist,
                         KillSwitch(settings.kill_switch_path))

    notifier = hitl_notifier = None
    telegram = None
    if settings.telegram_token and settings.telegram_chat_id:
        from ..telegram.client import TelegramClient
        from ..telegram.format import format_hitl_request
        telegram = TelegramClient(settings.telegram_token,
                                  settings.telegram_chat_id)
        notifier = telegram.send_message

        def hitl_notifier(decision_id, order, reasons):
            telegram.send_message(
                format_hitl_request(decision_id, order, reasons),
                buttons=[("Aprovar", f"ap:{decision_id}"),
                         ("Rejeitar", f"rj:{decision_id}")])

    news: list[tuple] = []
    macro: dict[str, float] = {}
    proposer = hold_proposer
    if args.llm:
        if not settings.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY ausente — necessário para --llm")
        from ..brain.client import LlmClient
        from ..brain.enrich import enrich_news
        from ..brain.proposer import make_llm_proposer
        client = LlmClient(api_key=settings.anthropic_api_key)
        if llm_pre_gates(store, settings, now):
            enrich_news(client, store, now)
            proposer = make_llm_proposer(client, store, lambda: now)
            news = store.recent_news(now - timedelta(hours=24))
            macro = {name: point.value
                     for name in ("fng", "selic", "cambio")
                     if (point := store.latest_macro(name)) is not None}
            if telegram is not None:
                positions = store.get_positions()
                material = [n for n in news
                           if n[5] is not None and n[5] >= 4
                           and n[2] and n[2][0] in positions][:3]
                for title, source, assets, _pub, _sent, materiality in material:
                    from ..telegram.format import format_material_news
                    _notify(notifier, format_material_news(
                        title, source, assets[0], materiality))

    result = run_cycle(store, candle_store, adapter, engine, proposer,
                       settings, now, dry_run=args.dry_run,
                       news=news, macro=macro, notifier=notifier,
                       hitl_notifier=hitl_notifier)
    print(f"ciclo {result.cycle_id}: {result.verdict_status}"
          + (f" ({'; '.join(result.reasons)})" if result.reasons else "")
          + (" — ordem executada" if result.executed else ""))
    if telegram is not None:
        from ..telegram.format import format_cycle_result
        # CycleResult não carrega a ordem executada; a mensagem detalhada
        # de execução já é coberta pelos notifiers internos do ciclo
        # (ordem aprovada executada / HITL). Aqui é só o resumo do status.
        _notify(notifier, format_cycle_result(result, None))
    store.close()


if __name__ == "__main__":
    main()
