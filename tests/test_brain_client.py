import json
from types import SimpleNamespace

import pytest

from invest_agent.brain.client import (
    PRICES, LlmClient, LlmError, usage_cost_usd,
)

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}},
          "required": ["x"], "additionalProperties": False}


def _usage(inp=1000, out=500, cache_read=0, cache_write=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_read_input_tokens=cache_read,
                           cache_creation_input_tokens=cache_write)


def _response(text='{"x": 1}', stop_reason="end_turn", usage=None):
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], stop_reason=stop_reason,
                           usage=usage or _usage())


def test_usage_cost_usd_haiku():
    # 1000 in + 500 out no haiku: 1000/1e6*1.0 + 500/1e6*5.0 = 0.0035
    assert usage_cost_usd("claude-haiku-4-5", _usage()) == pytest.approx(0.0035)


def test_usage_cost_usd_com_cache():
    # sonnet: in 1000*3/1e6 + out 500*15/1e6 + read 2000*0.1*3/1e6 + write 1000*2*3/1e6
    usage = _usage(1000, 500, cache_read=2000, cache_write=1000)
    esperado = (1000 * 3.0 + 500 * 15.0 + 2000 * 0.3 + 1000 * 6.0) / 1e6
    assert usage_cost_usd("claude-sonnet-5", usage) == pytest.approx(esperado)


def test_structured_parseia_e_custa():
    calls = []

    def create_fn(**kwargs):
        calls.append(kwargs)
        return _response()

    client = LlmClient(create_fn=create_fn)
    data, cost = client.structured("claude-haiku-4-5", "sistema",
                                   {"b": 2, "a": 1}, SCHEMA, max_tokens=500)
    assert data == {"x": 1} and cost == pytest.approx(0.0035)
    (kwargs,) = calls
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["max_tokens"] == 500
    assert kwargs["output_config"] == {"format": {"type": "json_schema",
                                                  "schema": SCHEMA}}
    system_block = kwargs["system"][0]
    assert system_block["text"] == "sistema"
    assert system_block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    user_text = kwargs["messages"][0]["content"]
    assert json.loads(user_text) == {"a": 1, "b": 2}
    assert user_text.index('"a"') < user_text.index('"b"')  # sort_keys


def test_structured_refusal_vira_none_com_custo():
    client = LlmClient(create_fn=lambda **k: _response(
        text="", stop_reason="refusal"))
    data, cost = client.structured("claude-sonnet-5", "s", {}, SCHEMA, 100)
    assert data is None and cost > 0


def test_structured_json_invalido_vira_none():
    client = LlmClient(create_fn=lambda **k: _response(text="não é json"))
    data, cost = client.structured("claude-haiku-4-5", "s", {}, SCHEMA, 100)
    assert data is None and cost > 0


def test_erro_de_api_vira_llm_error():
    def create_fn(**kwargs):
        raise RuntimeError("boom")

    client = LlmClient(create_fn=create_fn)
    with pytest.raises(LlmError, match="falha na chamada"):
        client.structured("claude-haiku-4-5", "s", {}, SCHEMA, 100)


def test_precos_da_spec():
    assert PRICES["claude-sonnet-5"] == (3.0, 15.0)
    assert PRICES["claude-haiku-4-5"] == (1.0, 5.0)
    assert PRICES["claude-opus-5"] == (5.0, 25.0)
