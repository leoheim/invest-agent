# tests/test_brain_triage.py
from invest_agent.brain.client import LlmClient
from invest_agent.brain.triage import (
    TRIAGE_MODEL, TRIAGE_SCHEMA, triage_candidates,
)
from types import SimpleNamespace
import json


def _client(payload, stop_reason="end_turn"):
    def create_fn(**kwargs):
        create_fn.kwargs = kwargs
        block = SimpleNamespace(type="text", text=json.dumps(payload))
        usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(content=[block], stop_reason=stop_reason,
                               usage=usage)
    client = LlmClient(create_fn=create_fn)
    client._fn = create_fn
    return client


CTX = {"whitelist": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "symbols": {}}


def test_triagem_filtra_whitelist_dedupe_e_limita_a_3():
    payload = {"candidates": [
        {"symbol": "BTCUSDT", "reason": "a"},
        {"symbol": "FORAUSDT", "reason": "b"},   # fora da whitelist
        {"symbol": "BTCUSDT", "reason": "c"},    # duplicado
        {"symbol": "ETHUSDT", "reason": "d"},
        {"symbol": "SOLUSDT", "reason": "e"},
    ]}
    client = _client(payload)
    symbols, cost = triage_candidates(client, CTX)
    assert symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert cost > 0
    assert client._fn.kwargs["model"] == TRIAGE_MODEL


def test_triagem_refusal_vira_lista_vazia():
    client = _client({}, stop_reason="refusal")
    symbols, cost = triage_candidates(client, CTX)
    assert symbols == [] and cost > 0


def test_schema_e_strito():
    assert TRIAGE_SCHEMA["additionalProperties"] is False
    item = TRIAGE_SCHEMA["properties"]["candidates"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"symbol", "reason"}
