# src/invest_agent/brain/triage.py
"""Passo 2 do ciclo (spec §4.3): Haiku 4.5 tria a whitelist e devolve até
3 candidatos. O código impõe as regras duras (whitelist, dedupe, teto de
3) — o LLM só ordena por interesse; refusal/parse inválido = sem
candidatos (ciclo tende a HOLD)."""
from __future__ import annotations

from .client import LlmClient

TRIAGE_MODEL = "claude-haiku-4-5"

TRIAGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["symbol", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

TRIAGE_SYSTEM = (
    "Você é o triador de um agente de investimento em cripto (spot, "
    "Binance). Receberá um contexto JSON com a whitelist, indicadores por "
    "símbolo, notícias enriquecidas e macro. Escolha até 3 símbolos DA "
    "WHITELIST que mereçam análise neste ciclo (momentum, notícia "
    "material, movimento relevante), do mais ao menos interessante, cada "
    "um com uma razão curta em português. Sem sinal claro, devolva lista "
    "vazia — não force candidatos."
)


def triage_candidates(client: LlmClient,
                      context: dict) -> tuple[list[str], float]:
    data, cost = client.structured(TRIAGE_MODEL, TRIAGE_SYSTEM, context,
                                   TRIAGE_SCHEMA, max_tokens=1000)
    if not data:
        return [], cost
    whitelist = set(context.get("whitelist", []))
    out: list[str] = []
    for entry in data.get("candidates", []):
        symbol = entry.get("symbol")
        if symbol in whitelist and symbol not in out:
            out.append(symbol)
        if len(out) == 3:
            break
    return out, cost
