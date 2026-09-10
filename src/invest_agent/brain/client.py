"""Wrapper testável da API da Claude. create_fn injetável — nenhum teste
toca a rede. Structured outputs (output_config.format) garantem JSON
válido; refusal ou parse inválido viram (None, custo) — 'falha = ciclo
sem ação' (spec §4.3). Custo aproximado da usage com preços de lista:
cache read 0.1×, cache write 2× (TTL 1h)."""
from __future__ import annotations

import json
from typing import Callable

PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-5": (5.0, 25.0),
}

_CACHE_READ_MULT = 0.1
_CACHE_WRITE_MULT = 2.0  # TTL 1h


class LlmError(Exception):
    """Falha de transporte/API — o chamador converte em HOLD."""


def usage_cost_usd(model: str, usage) -> float:
    price_in, price_out = PRICES[model]
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (usage.input_tokens * price_in
            + usage.output_tokens * price_out
            + cache_read * _CACHE_READ_MULT * price_in
            + cache_write * _CACHE_WRITE_MULT * price_in) / 1_000_000


def _default_create_fn(api_key: str) -> Callable:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    return client.messages.create


class LlmClient:
    def __init__(self, create_fn: Callable | None = None, api_key: str = ""):
        self._create = create_fn or _default_create_fn(api_key)

    def structured(self, model: str, system: str, user_payload: dict,
                   schema: dict, max_tokens: int,
                   cache_ttl: str = "1h") -> tuple[dict | None, float]:
        try:
            response = self._create(
                model=model,
                max_tokens=max_tokens,
                system=[{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral", "ttl": cache_ttl},
                }],
                messages=[{
                    "role": "user",
                    "content": json.dumps(user_payload, sort_keys=True,
                                          ensure_ascii=False),
                }],
                output_config={"format": {"type": "json_schema",
                                          "schema": schema}},
            )
        except Exception as err:
            raise LlmError(f"falha na chamada ao modelo {model}: {err}") from err
        cost = usage_cost_usd(model, response.usage)
        if response.stop_reason == "refusal":
            return None, cost
        text = next((b.text for b in response.content
                     if getattr(b, "type", "") == "text"), "")
        try:
            return json.loads(text), cost
        except (json.JSONDecodeError, TypeError):
            return None, cost
