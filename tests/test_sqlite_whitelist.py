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


def test_claim_pending_e_atomico(tmp_path):
    # I2: só a primeira reivindicação de um decision_id 'approved' pode
    # ganhar — a segunda (simulando um processo concorrente) tem que
    # falhar, senão dois ciclos podem enviar a mesma ordem duas vezes.
    store = SqliteStore(tmp_path / "a.db")
    store.add_pending("d1", NOW, NOW)
    store.set_pending_status("d1", "approved")
    assert store.claim_pending("d1") is True
    assert store.approved_pending() == []  # não está mais 'approved'
    assert store.claim_pending("d1") is False  # 2a reivindicação falha
    store.close()


def test_claim_pending_recusa_id_inexistente_ou_nao_aprovado(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    assert store.claim_pending("nao-existe") is False
    store.add_pending("d1", NOW, NOW)  # status='pending', não 'approved'
    assert store.claim_pending("d1") is False
    store.close()


def test_set_decision_fills_atualiza_so_essa_coluna(tmp_path):
    # C2: set_decision_fills é a única mutação permitida em decision_log —
    # o resto da linha (append-only) tem que continuar intacto.
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(DecisionRecord(
        decision_id="d1", ts=NOW, inputs_hash="h", snapshot_json="{}",
        proposal_json="{}", verdict_json="{}",
        order_json='{"symbol": "BTCUSDT"}'))
    store.set_decision_fills("d1", '{"executed_qty": 0.5}')
    rec = store.get_decision("d1")
    assert rec.fills_json == '{"executed_qty": 0.5}'
    assert rec.order_json == '{"symbol": "BTCUSDT"}'
    assert rec.inputs_hash == "h"
    store.close()


def test_last_executed_order_at_by_symbol_ignora_sem_fills(tmp_path):
    # C2: uma decisão com order_json mas sem fills_json (intenção
    # registrada, nunca executada) não pode contar para o HITL obrigatório.
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(DecisionRecord(
        decision_id="d1", ts=NOW, inputs_hash="h", snapshot_json="{}",
        proposal_json="{}", verdict_json="{}",
        order_json='{"symbol": "BTCUSDT"}'))
    assert store.last_executed_order_at_by_symbol() == {}
    store.set_decision_fills("d1", '{"executed_qty": 1}')
    assert store.last_executed_order_at_by_symbol() == {"BTCUSDT": NOW}
    store.close()
