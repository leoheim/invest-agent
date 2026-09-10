from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from invest_agent.brain.weekly import (
    WEEKLY_MODEL, build_weekly_prompt, collect_weekly_review,
    submit_weekly_review,
)
from invest_agent.storage.sqlite_store import DecisionRecord, SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _decision(i, ts):
    return DecisionRecord(
        decision_id=f"d{i}", ts=ts, inputs_hash="h",
        snapshot_json="{}",
        proposal_json='{"symbol": "BTCUSDT", "action": "buy", "conviction": 0.3, "rationale": "sinal", "cycle_id": "c"}',
        verdict_json='{"status": "rejected", "reasons": ["cooldown ativo"]}')


def test_build_weekly_prompt_resume_decisoes():
    decisions = [_decision(1, NOW - timedelta(days=2)),
                 _decision(2, NOW - timedelta(days=1))]
    prompt = build_weekly_prompt(decisions, since=NOW - timedelta(days=7))
    assert "BTCUSDT" in prompt and "rejected" in prompt
    assert "cooldown ativo" in prompt


def test_submit_weekly_review(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_decision(1, NOW - timedelta(days=1)))
    created = {}

    class FakeBatches:
        def create(self, requests):
            created["requests"] = requests
            return SimpleNamespace(id="batch_123")

    batch_id = submit_weekly_review(FakeBatches(), store, NOW)
    assert batch_id == "batch_123"
    (req,) = created["requests"]
    assert req["custom_id"].startswith("weekly-")
    assert req["params"]["model"] == WEEKLY_MODEL
    store.close()


def test_submit_sem_decisoes_nao_submete(tmp_path):
    store = SqliteStore(tmp_path / "a.db")

    class Explode:
        def create(self, requests):
            raise AssertionError("não devia submeter")

    assert submit_weekly_review(Explode(), store, NOW) == ""
    store.close()


def test_collect_grava_learning(tmp_path):
    class FakeBatches:
        def retrieve(self, batch_id):
            return SimpleNamespace(processing_status="ended")

        def results(self, batch_id):
            block = SimpleNamespace(type="text", text="## Crítica\nOpere menos.")
            msg = SimpleNamespace(content=[block])
            yield SimpleNamespace(
                custom_id="weekly-2026-09-10",
                result=SimpleNamespace(type="succeeded",
                                       message=msg))

    path = collect_weekly_review(FakeBatches(), "batch_123",
                                 tmp_path / "learnings", NOW)
    assert path is not None and path.exists()
    assert "Opere menos" in path.read_text()
    assert path.name == "2026-09-10-revisao-semanal.md"


def test_collect_ainda_processando_devolve_none(tmp_path):
    class FakeBatches:
        def retrieve(self, batch_id):
            return SimpleNamespace(processing_status="in_progress")

    assert collect_weekly_review(FakeBatches(), "b", tmp_path, NOW) is None
