# tests/test_brain_proposer.py
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from invest_agent.brain.client import LlmClient, LlmError
from invest_agent.brain.proposer import (
    PROPOSAL_SCHEMA, PROPOSER_MODEL, make_llm_proposer, propose,
)
from invest_agent.models import Action
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
CTX = {"cycle_id": "2026091012", "whitelist": ["BTCUSDT", "ETHUSDT"],
       "symbols": {}}


def _client(payloads):
    """payloads: lista de respostas em ordem de chamada (dict, 'refusal'
    ou exceção)."""
    state = {"i": 0}

    def create_fn(**kwargs):
        payload = payloads[state["i"]]
        state["i"] += 1
        if isinstance(payload, Exception):
            raise payload
        usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if payload == "refusal":
            return SimpleNamespace(content=[], stop_reason="refusal",
                                   usage=usage)
        block = SimpleNamespace(type="text", text=json.dumps(payload))
        return SimpleNamespace(content=[block], stop_reason="end_turn",
                               usage=usage)

    return LlmClient(create_fn=create_fn)


def test_propose_feliz():
    client = _client([{"symbol": "BTCUSDT", "action": "buy",
                       "conviction": 0.4, "rationale": "momentum",
                       "urgency": "media"}])
    proposal, cost = propose(client, CTX, ["BTCUSDT"])
    assert proposal.action is Action.BUY and proposal.symbol == "BTCUSDT"
    assert proposal.conviction == 0.4 and proposal.cycle_id == "2026091012"
    assert cost > 0


def test_propose_sem_candidatos_e_hold_sem_chamada():
    client = _client([])  # qualquer chamada estouraria IndexError
    proposal, cost = propose(client, CTX, [])
    assert proposal.action is Action.HOLD and cost == 0.0


def test_propose_refusal_vira_hold():
    client = _client(["refusal"])
    proposal, _ = propose(client, CTX, ["BTCUSDT"])
    assert proposal.action is Action.HOLD
    assert "sem ação" in proposal.rationale


def test_propose_simbolo_fora_dos_candidatos_vira_hold():
    client = _client([{"symbol": "ETHUSDT", "action": "buy",
                       "conviction": 0.5, "rationale": "x",
                       "urgency": "alta"}])
    proposal, _ = propose(client, CTX, ["BTCUSDT"])
    assert proposal.action is Action.HOLD


def test_propose_conviction_fora_da_faixa_e_clampada():
    client = _client([{"symbol": "BTCUSDT", "action": "buy",
                       "conviction": 1.7, "rationale": "x",
                       "urgency": "alta"}])
    proposal, _ = propose(client, CTX, ["BTCUSDT"])
    assert proposal.conviction == 1.0


def test_make_llm_proposer_grava_custo_e_propaga(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    # 1ª chamada: triagem devolve BTCUSDT; 2ª: proposta buy
    client = _client([
        {"candidates": [{"symbol": "BTCUSDT", "reason": "r"}]},
        {"symbol": "BTCUSDT", "action": "buy", "conviction": 0.3,
         "rationale": "x", "urgency": "baixa"},
    ])
    proposer = make_llm_proposer(client, store, lambda: NOW)
    proposal = proposer(CTX)
    assert proposal.action is Action.BUY
    assert store.api_cost_today(NOW.date()) > 0
    store.close()


def test_make_llm_proposer_llm_error_vira_hold(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    client = _client([RuntimeError("rede caiu")])
    proposer = make_llm_proposer(client, store, lambda: NOW)
    proposal = proposer(CTX)
    assert proposal.action is Action.HOLD
    assert "falha de LLM" in proposal.rationale
    store.close()


def test_schema_do_proposal():
    props = PROPOSAL_SCHEMA["properties"]
    assert props["action"]["enum"] == ["buy", "sell", "close", "hold"]
    assert set(PROPOSAL_SCHEMA["required"]) == {
        "symbol", "action", "conviction", "rationale", "urgency"}
    assert PROPOSAL_SCHEMA["additionalProperties"] is False
    assert PROPOSER_MODEL == "claude-sonnet-5"
