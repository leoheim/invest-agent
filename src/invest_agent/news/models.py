"""Modelo de notícia. published_at (do feed) e ingested_at (nosso relógio)
são campos SEPARADOS — anti look-ahead da spec §4.1: em backtest/análise
retroativa, só vale o que já estava publicado E ingerido no momento."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from .canonical import canonical_url


@dataclass(frozen=True, slots=True)
class NewsItem:
    id: str
    source: str
    title: str
    url: str
    published_at: datetime | None
    ingested_at: datetime
    summary: str


def make_news_item(source: str, title: str, url: str,
                   published_at: datetime | None, ingested_at: datetime,
                   summary: str) -> NewsItem:
    canon = canonical_url(url)
    item_id = hashlib.sha1(canon.encode()).hexdigest()
    return NewsItem(id=item_id, source=source, title=title.strip(),
                    url=canon, published_at=published_at,
                    ingested_at=ingested_at, summary=summary.strip())
