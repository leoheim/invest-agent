"""Pipeline de ingestão de notícias (spec §4.1): buscar feeds → parsear →
triagem por keyword → dedupe (URL canônica → SimHash) → persistir com
published_at ≠ ingested_at. O main() do CLI é a única borda com relógio
e rede reais. Dedupe por embedding e enriquecimento LLM: Fase 2."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..storage.sqlite_store import SqliteStore
from .canonical import is_near_duplicate, simhash64
from .rss import FEEDS, parse_feed
from .triage import triage


def ingest_news(store: SqliteStore, feeds: dict[str, str],
                transport: Callable[[str], bytes],
                now: datetime) -> dict:
    stats = {"fetched": 0, "kept": 0, "inserted": 0, "dup_url": 0,
             "dup_simhash": 0, "feeds_failed": 0}
    items = []
    for source, url in feeds.items():
        try:
            payload = transport(url)
        except Exception:
            stats["feeds_failed"] += 1
            continue
        items.extend(parse_feed(payload, source, now))
    stats["fetched"] = len(items)

    kept = triage(items)
    stats["kept"] = len(kept)

    known_urls = store.known_urls()
    known_hashes = store.known_simhashes()
    to_insert = []
    for item, assets in kept:
        if item.url in known_urls:
            stats["dup_url"] += 1
            continue
        item_hash = simhash64(item.title)
        if any(is_near_duplicate(item_hash, h) for h in known_hashes):
            stats["dup_simhash"] += 1
            continue
        known_urls.add(item.url)
        known_hashes.append(item_hash)
        to_insert.append((item, assets))
    stats["inserted"] = store.insert_news(to_insert)
    return stats


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Ingestão de notícias (RSS) e macro (F&G, BCB)")
    parser.add_argument("--db", default=Path("data/agent.db"), type=Path)
    parser.add_argument("--macro", action="store_true",
                        help="também busca Fear & Greed, Selic e câmbio")
    args = parser.parse_args(argv)

    import urllib.request

    def transport(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "invest-agent/0.1"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteStore(args.db)
    now = datetime.now(timezone.utc)  # borda de composição
    stats = ingest_news(store, FEEDS, transport, now)
    print(f"notícias: {stats['inserted']} novas de {stats['fetched']} lidas "
          f"({stats['kept']} após triagem; dups: {stats['dup_url']} url, "
          f"{stats['dup_simhash']} simhash; feeds com falha: "
          f"{stats['feeds_failed']})")
    if args.macro:
        from ..macro.fetchers import (SGS_CAMBIO, SGS_SELIC, fetch_bcb_sgs,
                                      fetch_fear_greed)
        for point in (fetch_fear_greed(transport),
                      fetch_bcb_sgs(SGS_SELIC, "selic", transport),
                      fetch_bcb_sgs(SGS_CAMBIO, "cambio", transport)):
            store.upsert_macro(point)
            print(f"macro {point.series}: {point.value} ({point.date})")
    store.close()


if __name__ == "__main__":
    main()
