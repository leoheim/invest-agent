"""Bot de comandos (spec §4.6): /status /perfil /pausar /retomar /kill
/aprovar /rejeitar + botões inline. Processo separado do ciclo (systemd);
o bot só marca aprovações — a execução acontece no ciclo seguinte.
Kill switch e halt são arquivos/SQLite compartilhados — nenhum estado
em memória além do offset."""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from typing import Callable

from ..config import MODERADO
from ..killswitch import KillSwitch
from ..storage.sqlite_store import SqliteStore
from .client import TelegramClient
from .format import build_status_text

AJUDA = ("Comandos: /status /perfil /pausar /retomar /kill "
         "/aprovar <id> /rejeitar <id>")


def _perfil() -> str:
    p = MODERADO
    return (f"Perfil {p.name}: máx {p.max_position_pct:.0%} por ativo · "
            f"máx {p.max_exposure_pct:.0%} investido · stop "
            f"{p.stop_loss_pct:.0%} · ≤{p.max_orders_per_day} ordens/dia · "
            f"cooldown {p.cooldown_hours:g}h · HITL "
            f"{p.hitl_threshold_pct:.0%} · halt "
            f"{p.daily_loss_halt_pct:.0%}/{p.weekly_loss_halt_pct:.0%}/"
            f"{p.monthly_loss_halt_pct:.0%}")


def _decide(store: SqliteStore, decision_id: str, status: str,
            now: datetime) -> str:
    pending = {p[0]: p for p in store.get_pending()}
    entry = pending.get(decision_id)
    if entry is None:
        return f"pendência {decision_id} não encontrada (já decidida?)"
    if entry[2] < now:
        store.set_pending_status(decision_id, "expired")
        return f"pendência {decision_id} expirada — ordem cancelada"
    store.set_pending_status(decision_id, status)
    verbo = "aprovada" if status == "approved" else "rejeitada"
    extra = (" — executa no próximo ciclo" if status == "approved" else "")
    return f"pendência {decision_id} {verbo}{extra}"


def handle_update(update: dict, store: SqliteStore, kill_switch: KillSwitch,
                  client: TelegramClient, now: datetime) -> str | None:
    callback = update.get("callback_query")
    if callback:
        data = callback.get("data", "")
        client.answer_callback(callback["id"])
        if data.startswith("ap:"):
            reply = _decide(store, data[3:], "approved", now)
        elif data.startswith("rj:"):
            reply = _decide(store, data[3:], "rejected", now)
        else:
            reply = AJUDA
        client.send_message(reply)
        return reply

    text = (update.get("message") or {}).get("text", "").strip()
    if not text:
        return None
    parts = text.split()
    command, args = parts[0], parts[1:]
    if command == "/status":
        reply = build_status_text(store, MODERADO, now)
    elif command == "/perfil":
        reply = _perfil()
    elif command == "/pausar":
        kill_switch.activate("pausado via telegram")
        reply = "⏸️ pausado — kill switch ativo"
    elif command == "/retomar":
        kill_switch.deactivate()
        store.release_halt()
        reply = "▶️ retomado — kill switch e halt liberados"
    elif command == "/kill":
        kill_switch.activate("kill via telegram")
        reply = "🛑 kill switch ATIVADO"
    elif command == "/aprovar" and args:
        reply = _decide(store, args[0], "approved", now)
    elif command == "/rejeitar" and args:
        reply = _decide(store, args[0], "rejected", now)
    else:
        reply = AJUDA
    client.send_message(reply)
    return reply


def run_bot(store: SqliteStore, kill_switch: KillSwitch,
            client: TelegramClient, clock: Callable[[], datetime],
            once: bool = False) -> None:
    offset = 0
    while True:
        updates, offset = client.get_updates(offset)
        for update in updates:
            handle_update(update, store, kill_switch, client, clock())
        if once:
            return


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bot Telegram do agente")
    parser.add_argument("--digest", action="store_true",
                        help="envia o status e sai (cron 9h)")
    args = parser.parse_args(argv)

    from ..settings import Settings

    settings = Settings.from_env(os.environ)  # borda única
    if not settings.telegram_token or not settings.telegram_chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ausentes")
    store = SqliteStore(settings.db_path)
    client = TelegramClient(settings.telegram_token,
                            settings.telegram_chat_id)
    if args.digest:
        client.send_message(build_status_text(
            store, MODERADO, datetime.now(timezone.utc)))
        store.close()
        return
    kill_switch = KillSwitch(settings.kill_switch_path)
    try:
        run_bot(store, kill_switch, client,
                clock=lambda: datetime.now(timezone.utc))
    finally:
        store.close()


if __name__ == "__main__":
    main()
