# tests/test_brain_enrich.py
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from invest_agent.brain.client import LlmClient
from invest_agent.brain.enrich import ENRICH_SCHEMA, enrich_news
from invest_agent.news.models import make_news_item
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _insert(store, title, url):
    item = make_news_item("src", title, url, NOW, NOW, "resumo bruto")
    store.insert_news([(item, ("BTCUSDT",))])
    return item


def _client(payload, stop_reason="end_turn"):
    def create_fn(**kwargs):
        usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if stop_reason == "refusal":
            return SimpleNamespace(content=[], stop_reason="refusal",
                                   usage=usage)
        block = SimpleNamespace(type="text", text=json.dumps(payload))
        return SimpleNamespace(content=[block], stop_reason="end_turn",
                               usage=usage)
    return LlmClient(create_fn=create_fn)


def test_enrich_aplica_e_custa(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    a = _insert(store, "Bitcoin sobe", "https://x.com/1")
    b = _insert(store, "ETF aprovado", "https://x.com/2")
    payload = {"items": [
        {"id": a.id, "summary": "alta forte", "sentiment": "positivo",
         "materiality": 4},
        {"id": "id-desconhecido", "summary": "x", "sentiment": "neutro",
         "materiality": 1},  # ignorado: não estava pendente
        {"id": b.id, "summary": "etf ok", "sentiment": "positivo",
         "materiality": 9},  # clampado para 5
    ]}
    stats = enrich_news(_client(payload), store, NOW)
    assert stats["pending"] == 2 and stats["enriched"] == 2
    assert stats["cost_usd"] > 0
    assert store.unenriched_news() == []
    recent = {t[0]: t for t in store.recent_news(NOW - timedelta(hours=1))}
    assert recent["ETF aprovado"][5] == 5  # materiality clampada
    assert store.api_cost_today(NOW.date()) > 0
    store.close()


def test_enrich_sem_pendentes_nao_chama(tmp_path):
    store = SqliteStore(tmp_path / "a.db")

    def explode(**kwargs):
        raise AssertionError("não devia chamar o LLM")

    stats = enrich_news(LlmClient(create_fn=explode), store, NOW)
    assert stats == {"pending": 0, "enriched": 0, "cost_usd": 0.0}
    store.close()


def test_enrich_refusal_mantem_pendentes(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    _insert(store, "t", "https://x.com/1")
    stats = enrich_news(_client({}, stop_reason="refusal"), store, NOW)
    assert stats["enriched"] == 0 and len(store.unenriched_news()) == 1
    store.close()


def test_schema_estrito():
    item = ENRICH_SCHEMA["properties"]["items"]["items"]
    assert item["properties"]["sentiment"]["enum"] == [
        "positivo", "negativo", "neutro"]
    assert item["additionalProperties"] is False
