import sqlite3
from datetime import date, datetime, timezone

import pytest

from invest_agent.news.models import make_news_item
from invest_agent.storage.sqlite_store import (
    DecisionRecord, MacroPoint, SqliteStore,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _item(title: str, url: str):
    return make_news_item("src", title, url, NOW, NOW, "resumo")


def test_insert_news_dedupe_por_id(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    item = _item("t1", "https://x.com/1")
    assert store.insert_news([(item, ("BTCUSDT",))]) == 1
    assert store.insert_news([(item, ("BTCUSDT",))]) == 0  # mesmo id
    assert store.known_urls() == {"https://x.com/1"}
    assert len(store.known_simhashes()) == 1
    store.close()


def test_macro_upsert_e_leitura(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_macro(MacroPoint("selic", date(2026, 9, 9), 15.0))
    store.upsert_macro(MacroPoint("selic", date(2026, 9, 9), 14.75))  # upsert
    latest = store.latest_macro("selic")
    assert latest == MacroPoint("selic", date(2026, 9, 9), 14.75)
    assert store.latest_macro("fng") is None
    store.close()


def _record(decision_id: str = "d1") -> DecisionRecord:
    return DecisionRecord(decision_id=decision_id, ts=NOW,
                          inputs_hash="abc", snapshot_json="{}",
                          proposal_json="{}", verdict_json="{}")


def test_decision_log_append_e_leitura(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_record())
    (lido,) = store.read_decisions()
    assert lido == _record()
    store.close()


def test_decision_log_id_duplicado_aborta(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_record())
    with pytest.raises(sqlite3.IntegrityError):
        store.append_decision(_record())
    store.close()


def test_decision_log_update_e_delete_abortam(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_record())
    raw = sqlite3.connect(tmp_path / "a.db")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute("UPDATE decision_log SET inputs_hash='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute("DELETE FROM decision_log")
    raw.close()
    store.close()


def test_reabrir_o_banco_preserva_dados(tmp_path):
    path = tmp_path / "a.db"
    store = SqliteStore(path)
    store.insert_news([(_item("t", "https://x.com/2"), ())])
    store.close()
    store2 = SqliteStore(path)
    assert store2.known_urls() == {"https://x.com/2"}
    store2.close()
