# src/invest_agent/brain/weekly.py
"""Revisão semanal (spec §4.3): Opus 5 via Batch API (−50%) lê o log de
decisões da semana e escreve uma crítica em learnings/ (markdown + git —
o agente lê no início do ciclo em fases futuras). Fluxo em 2 passos por
design do Batch API: --submit (cron domingo) e --collect (cron segunda)."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..storage.sqlite_store import SqliteStore

WEEKLY_MODEL = "claude-opus-5"

WEEKLY_SYSTEM = (
    "Você é o revisor semanal de um agente de investimento em cripto. "
    "Analise o log de decisões da semana (propostas do LLM e vereditos do "
    "motor de regras) e escreva uma crítica construtiva em markdown, em "
    "português: padrões de erro (overtrading? convicção mal calibrada? "
    "propostas rejeitadas repetidamente pelo mesmo motivo?), o que manter, "
    "e no máximo 3 recomendações concretas para a semana seguinte."
)


def build_weekly_prompt(decisions: list, since: datetime) -> str:
    lines = [f"Decisões desde {since:%Y-%m-%d}:"]
    for record in decisions:
        proposal = json.loads(record.proposal_json)
        verdict = json.loads(record.verdict_json)
        reasons = "; ".join(verdict.get("reasons", []))
        lines.append(
            f"- {record.ts:%Y-%m-%d %H:%M} {proposal.get('symbol')} "
            f"{proposal.get('action')} conv={proposal.get('conviction')} → "
            f"{verdict.get('status')}" + (f" ({reasons})" if reasons else ""))
    return "\n".join(lines)


def submit_weekly_review(batches, store: SqliteStore, now: datetime) -> str:
    since = now - timedelta(days=7)
    decisions = [d for d in store.read_decisions() if d.ts >= since]
    if not decisions:
        return ""
    prompt = build_weekly_prompt(decisions, since)
    batch = batches.create(requests=[{
        "custom_id": f"weekly-{now:%Y-%m-%d}",
        "params": {
            "model": WEEKLY_MODEL,
            "max_tokens": 8000,
            "system": WEEKLY_SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        },
    }])
    return batch.id


def collect_weekly_review(batches, batch_id: str, learnings_dir: Path,
                          now: datetime) -> Path | None:
    if batches.retrieve(batch_id).processing_status != "ended":
        return None
    for result in batches.results(batch_id):
        if result.result.type != "succeeded":
            continue
        text = next((b.text for b in result.result.message.content
                     if getattr(b, "type", "") == "text"), "")
        learnings_dir.mkdir(parents=True, exist_ok=True)
        path = learnings_dir / f"{now:%Y-%m-%d}-revisao-semanal.md"
        path.write_text(text, encoding="utf-8")
        return path
    return None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Revisão semanal (Opus)")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--collect", metavar="BATCH_ID")
    parser.add_argument("--db", default=Path("data/agent.db"), type=Path)
    parser.add_argument("--learnings", default=Path("learnings"), type=Path)
    args = parser.parse_args(argv)

    import anthropic

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    batches = client.messages.batches
    now = datetime.now(timezone.utc)  # borda de composição
    if args.submit:
        store = SqliteStore(args.db)
        batch_id = submit_weekly_review(batches, store, now)
        store.close()
        print(f"batch submetido: {batch_id}" if batch_id
              else "sem decisões na semana — nada a revisar")
    elif args.collect:
        path = collect_weekly_review(batches, args.collect, args.learnings, now)
        print(f"learning gravado: {path}" if path
              else "batch ainda processando — tente mais tarde")
    else:
        parser.error("use --submit ou --collect BATCH_ID")


if __name__ == "__main__":
    main()
