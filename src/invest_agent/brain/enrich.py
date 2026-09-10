# src/invest_agent/brain/enrich.py
"""Enriquecimento de notícias (spec §4.1): resumo, sentimento e
materialidade 1-5, em UMA chamada Haiku por lote — barato e idempotente
(o que falhar continua pendente para o próximo ciclo). As notícias
entram como campos estruturados, nunca texto concatenado."""
from __future__ import annotations

from datetime import datetime

from ..storage.sqlite_store import SqliteStore
from .client import LlmClient

ENRICH_MODEL = "claude-haiku-4-5"

ENRICH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "summary": {"type": "string"},
                    "sentiment": {"type": "string",
                                  "enum": ["positivo", "negativo", "neutro"]},
                    "materiality": {"type": "integer",
                                    "enum": [1, 2, 3, 4, 5]},
                },
                "required": ["id", "summary", "sentiment", "materiality"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

ENRICH_SYSTEM = (
    "Você enriquece notícias para um agente de investimento em cripto. "
    "Para CADA item recebido (id, título, resumo bruto), devolva: resumo "
    "de 1 frase em português, sentimento para o mercado cripto "
    "(positivo/negativo/neutro) e materialidade 1-5 (5 = move mercado "
    "hoje; 1 = irrelevante). Devolva exatamente um item por id recebido."
)


def enrich_news(client: LlmClient, store: SqliteStore, now: datetime,
                limit: int = 20) -> dict:
    pending = store.unenriched_news(limit=limit)
    if not pending:
        return {"pending": 0, "enriched": 0, "cost_usd": 0.0}
    payload = {"noticias": [
        {"id": news_id, "titulo": title, "resumo_bruto": summary}
        for news_id, title, summary in pending
    ]}
    data, cost = client.structured(ENRICH_MODEL, ENRICH_SYSTEM, payload,
                                   ENRICH_SCHEMA, max_tokens=4000)
    if cost > 0:
        store.add_api_cost(now.date(), cost)
    if not data:
        return {"pending": len(pending), "enriched": 0, "cost_usd": cost}
    valid_ids = {p[0] for p in pending}
    enriched = 0
    for item in data.get("items", []):
        if item.get("id") not in valid_ids:
            continue
        materiality = min(5, max(1, int(item["materiality"])))
        store.set_news_enrichment(item["id"], item["summary"],
                                  item["sentiment"], materiality)
        enriched += 1
    return {"pending": len(pending), "enriched": enriched, "cost_usd": cost}
