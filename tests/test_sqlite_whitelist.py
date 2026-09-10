# tests/test_sqlite_whitelist.py
from datetime import datetime, timezone

from invest_agent.storage.sqlite_store import DecisionRecord, SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def test_whitelist_set_get_substitui(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    assert store.get_whitelist() == frozenset()
    store.set_whitelist(["BTCUSDT", "ETHUSDT"], NOW)
    assert store.get_whitelist() == frozenset({"BTCUSDT", "ETHUSDT"})
    store.set_whitelist(["SOLUSDT"], NOW)  # substitui, não acumula
    assert store.get_whitelist() == frozenset({"SOLUSDT"})
    store.close()


def test_approved_pending_e_get_decision(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record = DecisionRecord(decision_id="d1", ts=NOW, inputs_hash="h",
                            snapshot_json="{}", proposal_json="{}",
                            verdict_json="{}",
                            order_json='{"symbol": "BTCUSDT"}')
    store.append_decision(record)
    store.add_pending("d1", NOW, NOW)
    assert store.approved_pending() == []
    store.set_pending_status("d1", "approved")
    assert store.approved_pending() == ["d1"]
    lido = store.get_decision("d1")
    assert lido == record
    assert store.get_decision("nao-existe") is None
    store.set_pending_status("d1", "executed")
    assert store.approved_pending() == []
    store.close()
