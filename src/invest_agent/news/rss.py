"""Parser RSS 2.0 / Atom em stdlib (xml.etree) — ruling: sem feedparser,
zero deps novas. Feeds da spec §4.1; Google News RSS por ativo com
janela de 1 dia, pt-BR."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from .models import NewsItem, make_news_item

FEEDS: dict[str, str] = {
    "infomoney": "https://www.infomoney.com.br/feed/",
    "valor": "https://pox.globo.com/rss/valor",
    "moneytimes": "https://www.moneytimes.com.br/feed/",
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
}

_ATOM = "{http://www.w3.org/2005/Atom}"


def google_news_feed(query: str) -> str:
    q = quote(f"{query} when:1d")
    return (f"https://news.google.com/rss/search?q={q}"
            "&hl=pt-BR&gl=BR&ceid=BR:pt-419")


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_rfc822(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return _to_utc(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        return None


def _parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return _to_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def parse_feed(xml_bytes: bytes, source: str, now: datetime) -> list[NewsItem]:
    """Aceita RSS 2.0 e Atom. Entradas sem título ou sem link são puladas;
    published_at=None quando ausente/imparseável (ingested_at é sempre now)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out: list[NewsItem] = []
    for item in root.iter("item"):  # RSS 2.0
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        out.append(make_news_item(
            source, title, link, _parse_rfc822(item.findtext("pubDate")),
            now, item.findtext("description") or ""))
    for entry in root.iter(f"{_ATOM}entry"):  # Atom
        title = (entry.findtext(f"{_ATOM}title") or "").strip()
        link_el = entry.find(f"{_ATOM}link")
        link = (link_el.get("href") or "").strip() if link_el is not None else ""
        if not title or not link:
            continue
        published = (_parse_iso(entry.findtext(f"{_ATOM}published"))
                     or _parse_iso(entry.findtext(f"{_ATOM}updated")))
        out.append(make_news_item(
            source, title, link, published, now,
            entry.findtext(f"{_ATOM}summary") or ""))
    return out
