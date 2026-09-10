from datetime import date, datetime, timezone

from invest_agent.storage.sqlite_store import DecisionRecord, SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def test_positions_crud(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_position("BTCUSDT", 0.5, 100_000.0, "ia-abc-sl")
    store.upsert_position("ETHUSDT", 2.0, 4_000.0)
    assert store.get_positions() == {
        "BTCUSDT": (0.5, 100_000.0, "ia-abc-sl"),
        "ETHUSDT": (2.0, 4_000.0, None),
    }
    store.upsert_position("BTCUSDT", 0.7, 101_000.0, "ia-def-sl")  # upsert
    assert store.get_positions()["BTCUSDT"] == (0.7, 101_000.0, "ia-def-sl")
    store.delete_position("ETHUSDT")
    assert "ETHUSDT" not in store.get_positions()
    store.close()


def test_equity_marks(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    assert store.get_mark("day") is None
    store.set_mark("day", 10_000.0, NOW)
    value, opened_at = store.get_mark("day")
    assert value == 10_000.0 and opened_at == NOW
    store.set_mark("day", 11_000.0, NOW)  # substitui
    assert store.get_mark("day")[0] == 11_000.0
    store.close()


def test_halt_state(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    assert store.get_halt() is None
    store.set_halt("MONTH", NOW)
    level, set_at, released = store.get_halt()
    assert level == "MONTH" and set_at == NOW and released is False
    store.release_halt()
    assert store.get_halt()[2] is True
    store.set_halt("DAY", NOW)  # novo halt zera released
    assert store.get_halt() == ("DAY", NOW, False)
    store.close()


def test_pending_approvals(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.add_pending("d1", NOW, NOW)
    (pend,) = store.get_pending()
    assert pend == ("d1", NOW, NOW, "pending")
    store.set_pending_status("d1", "expired")
    assert store.get_pending() == []
    store.close()


def test_api_costs_soma_por_dia(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    hoje = date(2026, 9, 10)
    assert store.api_cost_today(hoje) == 0.0
    store.add_api_cost(hoje, 0.5)
    store.add_api_cost(hoje, 0.25)
    store.add_api_cost(date(2026, 9, 9), 9.0)  # ontem não conta
    assert store.api_cost_today(hoje) == 0.75
    store.close()


def _decision(decision_id: str, ts: datetime, order: str | None):
    return DecisionRecord(decision_id=decision_id, ts=ts, inputs_hash="h",
                          snapshot_json="{}", proposal_json="{}",
                          verdict_json="{}", order_json=order)


def test_contadores_derivados_do_decision_log(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_decision(
        "d1", NOW, '{"symbol": "BTCUSDT"}'))
    store.append_decision(_decision(
        "d2", NOW.replace(hour=13), '{"symbol": "ETHUSDT"}'))
    store.append_decision(_decision("d3", NOW.replace(hour=14), None))  # HOLD
    ontem = NOW.replace(day=9)
    store.append_decision(_decision("d0", ontem, '{"symbol": "BTCUSDT"}'))
    assert store.count_orders_on(date(2026, 9, 10)) == 2
    last = store.last_order_at_by_symbol()
    assert last["BTCUSDT"] == NOW  # d0 é mais antigo; NOW vence
    assert last["ETHUSDT"] == NOW.replace(hour=13)
    store.close()
