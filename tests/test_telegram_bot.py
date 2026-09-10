import json
from datetime import datetime, timedelta, timezone

from invest_agent.killswitch import KillSwitch
from invest_agent.storage.sqlite_store import SqliteStore
from invest_agent.telegram.bot import handle_update, run_bot
from invest_agent.telegram.client import TelegramClient

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _client(updates_batches):
    sent = []

    def transport(url, body):
        if "getUpdates" in url:
            batch = updates_batches.pop(0) if updates_batches else []
            return json.dumps({"ok": True, "result": batch}).encode()
        if body:
            sent.append(json.loads(body))
        return json.dumps({"ok": True}).encode()

    return TelegramClient("t", "42", transport=transport), sent


def _msg(text):
    return {"update_id": 1, "message": {"chat": {"id": 42}, "text": text}}


def _fx(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    kill = KillSwitch(tmp_path / "KILL")
    client, sent = _client([])
    return store, kill, client, sent


def test_status_e_perfil(tmp_path):
    store, kill, client, sent = _fx(tmp_path)
    out = handle_update(_msg("/status"), store, kill, client, NOW)
    assert "Status" in out
    out = handle_update(_msg("/perfil"), store, kill, client, NOW)
    assert "moderado" in out and "10%" in out
    store.close()


def test_pausar_retomar_kill(tmp_path):
    store, kill, client, sent = _fx(tmp_path)
    handle_update(_msg("/pausar"), store, kill, client, NOW)
    assert kill.is_active()
    handle_update(_msg("/retomar"), store, kill, client, NOW)
    assert not kill.is_active()
    handle_update(_msg("/kill"), store, kill, client, NOW)
    assert kill.is_active() and "kill" in kill.reason()
    store.close()


def test_aprovar_por_comando_e_por_callback(tmp_path):
    store, kill, client, sent = _fx(tmp_path)
    store.add_pending("d1", NOW, NOW + timedelta(minutes=10))
    store.add_pending("d2", NOW, NOW + timedelta(minutes=10))
    handle_update(_msg("/aprovar d1"), store, kill, client, NOW)
    assert store.approved_pending() == ["d1"]
    callback = {"update_id": 2, "callback_query": {
        "id": "cb1", "data": "rj:d2", "message": {"chat": {"id": 42}}}}
    handle_update(callback, store, kill, client, NOW)
    assert store.get_pending() == []  # d1 approved, d2 rejected
    store.close()


def test_aprovar_expirada_recusa(tmp_path):
    store, kill, client, sent = _fx(tmp_path)
    store.add_pending("d1", NOW - timedelta(hours=1),
                      NOW - timedelta(minutes=50))
    out = handle_update(_msg("/aprovar d1"), store, kill, client, NOW)
    assert "expirada" in out
    assert store.approved_pending() == []
    store.close()


def test_comando_desconhecido(tmp_path):
    store, kill, client, sent = _fx(tmp_path)
    out = handle_update(_msg("/foo"), store, kill, client, NOW)
    assert "comandos" in out.lower()
    store.close()


def test_run_bot_once_processa_lote(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    kill = KillSwitch(tmp_path / "KILL")
    client, sent = _client([[_msg("/status")]])
    run_bot(store, kill, client, clock=lambda: NOW, once=True)
    assert any("Status" in m.get("text", "") for m in sent)
    store.close()
