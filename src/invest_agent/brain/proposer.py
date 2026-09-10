# src/invest_agent/brain/proposer.py
"""Passo 3 do ciclo (spec §4.3): Sonnet 5 produz a Proposal tipada.
O LLM NUNCA vê chaves, nunca calcula tamanho de posição e o estado é
read-only (spec §3): recebe um dict de contexto pronto e devolve
{ativo, ação, convicção, racional, urgência}. Toda falha vira HOLD —
o ciclo nunca cai por causa do LLM."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from ..models import Action, Proposal
from .client import LlmClient, LlmError
from .triage import triage_candidates

PROPOSER_MODEL = "claude-sonnet-5"

PROPOSAL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "action": {"type": "string",
                   "enum": ["buy", "sell", "close", "hold"]},
        "conviction": {"type": "number"},
        "rationale": {"type": "string"},
        "urgency": {"type": "string", "enum": ["baixa", "media", "alta"]},
    },
    "required": ["symbol", "action", "conviction", "rationale", "urgency"],
    "additionalProperties": False,
}

PROPOSER_SYSTEM = (
    "Você é o analista de um agente de investimento pessoal em cripto "
    "(spot, Binance, perfil moderado). Você PROPÕE; um motor de regras "
    "determinístico decide — você nunca executa, nunca vê chaves e nunca "
    "calcula tamanho de posição. Receberá um contexto JSON (posições, "
    "equity, indicadores, notícias enriquecidas, macro) e uma lista de "
    "candidatos triados. Proponha UMA ação sobre UM dos candidatos: "
    "buy/sell/close com convicção 0-1 e racional curto em português, ou "
    "hold se nada justificar operar. O default é manter (hold) — "
    "overtrading é o erro número 1; só proponha operação com sinal claro. "
    "Nunca proponha símbolo fora dos candidatos."
)


def _hold(context: dict, rationale: str) -> Proposal:
    return Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale=rationale, cycle_id=context["cycle_id"])


def propose(client: LlmClient, context: dict,
            candidates: list[str]) -> tuple[Proposal, float]:
    if not candidates:
        return _hold(context, "sem candidatos na triagem"), 0.0
    payload = dict(context)
    payload["candidatos"] = candidates
    data, cost = client.structured(PROPOSER_MODEL, PROPOSER_SYSTEM, payload,
                                   PROPOSAL_SCHEMA, max_tokens=4000)
    if not data:
        return _hold(context,
                     "LLM recusou ou resposta inválida — ciclo sem ação"), cost
    action = Action(data["action"])
    if action is not Action.HOLD and data["symbol"] not in candidates:
        return _hold(context,
                     f"símbolo {data['symbol']} fora dos candidatos"), cost
    conviction = min(1.0, max(0.0, float(data["conviction"])))
    return Proposal(symbol=data["symbol"], action=action,
                    conviction=conviction, rationale=data["rationale"],
                    cycle_id=context["cycle_id"]), cost


def make_llm_proposer(client: LlmClient, store,
                      now_provider: Callable[[], datetime]):
    """Proposer (2a) real: triagem → proposta, com custo somado no store."""

    def proposer(context: dict) -> Proposal:
        total = 0.0
        try:
            candidates, cost = triage_candidates(client, context)
            total += cost
            proposal, cost = propose(client, context, candidates)
            total += cost
            return proposal
        except LlmError as err:
            return _hold(context, f"falha de LLM: {err}")
        finally:
            if total > 0:
                store.add_api_cost(now_provider().date(), total)

    return proposer
