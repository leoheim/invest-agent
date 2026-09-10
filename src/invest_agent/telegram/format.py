"""Mensagens do agente para o dono (spec §4.6), em português simples.
build_status_text usa só o que o store sabe (posições registradas, halt,
custo de API, pendências) — equity ao vivo exigiria o adapter e fica com
o ciclo."""
from __future__ import annotations

from datetime import datetime

from ..models import OrderIntent
from ..orchestrator.cycle import CycleResult
from ..orchestrator.state import active_halt
from ..breakers import HaltLevel
from ..storage.sqlite_store import SqliteStore


def format_cycle_result(result: CycleResult,
                        order: OrderIntent | None) -> str:
    if result.verdict_status == "halted":
        return f"⛔ ciclo {result.cycle_id}: halt — {'; '.join(result.reasons)}"
    if result.verdict_status == "rejected":
        return (f"🚫 ciclo {result.cycle_id}: proposta rejeitada — "
                f"{'; '.join(result.reasons)}")
    if result.verdict_status == "needs_approval":
        return f"⏳ ciclo {result.cycle_id}: ordem aguardando sua aprovação"
    if result.executed:
        if order is None:
            # I1: o caminho direto (BUY/SELL aprovado sem HITL, executado
            # no mesmo ciclo) não carrega a OrderIntent na CycleResult —
            # sem este ramo, a mensagem caía em "sem ação (hold)" com
            # dinheiro de fato movido.
            return f"✅ ciclo {result.cycle_id}: ordem executada"
        return (f"✅ ciclo {result.cycle_id}: ordem executada — "
                f"{order.side} {order.qty:g} {order.symbol} @ "
                f"{order.limit_price:g}"
                + (f" (stop {order.stop_loss_price:g})"
                   if order.stop_loss_price else ""))
    if any("dry-run" in reason for reason in result.reasons):
        return f"🧪 ciclo {result.cycle_id}: dry-run — ordem não enviada"
    return f"· ciclo {result.cycle_id}: sem ação (hold)"


def format_hitl_request(decision_id: str, order: OrderIntent,
                        reasons: list[str]) -> str:
    return (f"⏳ Aprovação necessária [{decision_id}]\n"
            f"{order.side} {order.qty:g} {order.symbol} @ "
            f"{order.limit_price:g}\n"
            f"Motivo: {'; '.join(reasons)}\n"
            f"Expira em 10 minutos. Use os botões ou "
            f"/aprovar {decision_id} · /rejeitar {decision_id}")


def format_breaker(level_name: str) -> str:
    return (f"⛔ Circuit breaker acionado (nível {level_name}). "
            "Novas ordens suspensas conforme o perfil de risco.")


def format_material_news(title: str, source: str, symbol: str,
                         materiality: int) -> str:
    return (f"📰 [{source}] materialidade {materiality} em {symbol}: "
            f"{title}")


def build_status_text(store: SqliteStore, profile, now: datetime) -> str:
    lines = [f"📊 Status — {now:%Y-%m-%d %H:%M} UTC",
             f"Perfil: {profile.name}"]
    positions = store.get_positions()
    if positions:
        lines.append("Posições registradas:")
        for symbol, (qty, avg_price, _) in sorted(positions.items()):
            lines.append(f"  {symbol}: {qty:g} @ {avg_price:g}")
    else:
        lines.append("Sem posições registradas.")
    halt = active_halt(store, now)
    lines.append(f"Halt: {halt.name}" if halt is not HaltLevel.NONE
                 else "Halt: nenhum")
    lines.append(f"Custo de API hoje: US$ {store.api_cost_today(now.date()):.2f}")
    pending = store.get_pending()
    if pending:
        ids = ", ".join(p[0] for p in pending)
        lines.append(f"Aprovações pendentes: {ids}")
    return "\n".join(lines)
