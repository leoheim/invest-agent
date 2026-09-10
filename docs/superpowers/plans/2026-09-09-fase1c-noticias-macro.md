# Fase 1c — Ingestão de Notícias + Macro + SQLite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pipeline de notícias (RSS/Atom → normalização → dedupe → triagem por keyword) e dados macro (Fear & Greed, BCB SGS) persistidos em SQLite com `published_at ≠ ingested_at` (anti look-ahead), mais o log de decisões append-only da spec §4.2 — completando a "Ingestão" da Fase 1 e deixando a Fase 2 com armazenamento pronto.

**Architecture:** Módulos novos `src/invest_agent/news/` (modelos, URL canônica + SimHash, parser RSS/Atom em stdlib, triagem por keyword, dedupe em estágios), `src/invest_agent/macro/` (fetchers F&G e BCB com transport injetável — mesmo padrão da Fase 1a) e `src/invest_agent/storage/` (SQLite via stdlib `sqlite3`: notícias, macro e decision log append-only com triggers que abortam UPDATE/DELETE). CLI como única borda com relógio/rede.

**Tech Stack:** Python ≥ 3.12, SOMENTE stdlib nesta fase (xml.etree, sqlite3, urllib, hashlib, email.utils) — zero deps novas; dev: pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-invest-agent-design.md` (§4.1 ingestão de notícias/macro, §4.2 SQLite + log append-only). **Rulings do controller (no ledger):** (1) parser RSS/Atom em stdlib — feedparser não é mandatado pela spec e a disciplina do projeto favorece zero deps; (2) o estágio de dedupe por embedding (>0.92) é DEFERIDO para a Fase 2 (exige modelo de embedding/API) — Fase 1c implementa URL canônica + SimHash, os dois primeiros estágios da spec; (3) o enriquecimento LLM em batch (resumo/sentimento/materialidade) é da Fase 2 por definição (zero LLM na Fase 1) — o schema já reserva as colunas.

## Global Constraints

- SOMENTE stdlib nesta fase (runtime deps do projeto permanecem duckdb, pyarrow, backtrader — nenhum é usado aqui).
- Nenhum teste acessa rede; todo HTTP passa por `transport: Callable[[str], bytes]` injetável; fixtures locais.
- Nenhum módulo lê o relógio na lógica (`now` sempre parâmetro); exceção única: `main()` do CLI.
- **Anti look-ahead:** toda notícia carrega `published_at` (do feed, pode ser None) E `ingested_at` (nosso relógio) separados — nunca um substitui o outro.
- `decision_log` é **append-only**: sem métodos de update/delete e com triggers SQLite abortando UPDATE/DELETE.
- Datas timezone-aware UTC (armazenadas como ISO-8601 com offset); mensagens em português; commits pequenos, sem assinatura.
- `news/`, `macro/` e `storage/` não importam o motor de regras nem `backtest/`; podem importar `data/` se preciso (não deve ser).

---

### Task 1: Modelo de notícia + URL canônica + SimHash

**Files:**
- Create: `src/invest_agent/news/__init__.py`
- Create: `src/invest_agent/news/models.py`
- Create: `src/invest_agent/news/canonical.py`
- Test: `tests/test_news_canonical.py`

**Interfaces:**
- Produces: `NewsItem` (dataclass frozen: `id: str` (sha1 da URL canônica), `source: str`, `title: str`, `url: str` (canônica), `published_at: datetime | None`, `ingested_at: datetime`, `summary: str`); `make_news_item(source, title, url, published_at, ingested_at, summary) -> NewsItem` (canoniza a URL e deriva o id); `canonical_url(url: str) -> str`; `simhash64(text: str) -> int`; `hamming(a: int, b: int) -> int`; `is_near_duplicate(a: int, b: int, threshold: int = 3) -> bool`. Tasks 2, 4 e 6 consomem estes nomes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_news_canonical.py
from datetime import datetime, timezone

from invest_agent.news.canonical import (
    canonical_url, hamming, is_near_duplicate, simhash64,
)
from invest_agent.news.models import make_news_item


def test_canonical_url_remove_tracking_ordena_e_normaliza():
    url = "HTTPS://Www.Site.com/Path/?utm_source=x&fbclid=abc&b=2&a=1#frag"
    assert canonical_url(url) == "https://www.site.com/Path?a=1&b=2"


def test_canonical_url_sem_query_remove_barra_final():
    assert canonical_url("https://site.com/noticia/") == "https://site.com/noticia"


def test_canonical_url_preserva_caminho_raiz():
    assert canonical_url("https://site.com/") == "https://site.com/"


def test_simhash_identico_e_zero_de_distancia():
    a = simhash64("Bitcoin sobe 5% após decisão do Fed")
    b = simhash64("Bitcoin sobe 5% após decisão do Fed")
    assert a == b and hamming(a, b) == 0


def test_simhash_textos_diferentes_ficam_longe():
    a = simhash64("Bitcoin sobe cinco por cento após decisão do Fed hoje")
    b = simhash64("Vale anuncia dividendos extraordinários para acionistas em dezembro")
    assert hamming(a, b) > 10


def test_hamming_e_threshold_deterministicos():
    assert hamming(0b0, 0b111) == 3
    assert is_near_duplicate(0b0, 0b111, threshold=3) is True
    assert is_near_duplicate(0b0, 0b1111, threshold=3) is False


def test_simhash_texto_vazio_e_zero():
    assert simhash64("") == 0


def test_make_news_item_canoniza_e_deriva_id():
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    item = make_news_item("coindesk", "Título", 
                          "https://site.com/x?utm_source=rss&a=1",
                          None, now, "resumo")
    outro = make_news_item("coindesk", "Título", "https://site.com/x?a=1",
                           None, now, "resumo")
    assert item.url == "https://site.com/x?a=1"
    assert item.id == outro.id and len(item.id) == 40  # sha1 hex
    assert item.published_at is None and item.ingested_at == now
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_news_canonical.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'invest_agent.news'`

- [ ] **Step 3: Write minimal implementation**

`src/invest_agent/news/__init__.py`: vazio.

```python
# src/invest_agent/news/canonical.py
"""Normalização de URL e SimHash de 64 bits — os dois primeiros estágios
do dedupe da spec §4.1 (URL canônica → SimHash do título). O terceiro
estágio (embedding >0.92) fica para a Fase 2 (exige modelo de embedding)."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"fbclid", "gclid", "ref", "cmpid", "sref"}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith(_TRACKING_PREFIXES)
             and k.lower() not in _TRACKING_PARAMS]
    query.sort()
    path = parts.path
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                       urlencode(query), ""))


def simhash64(text: str) -> int:
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return 0
    weights = [0] * 64
    for token in tokens:
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            weights[bit] += 1 if (value >> bit) & 1 else -1
    return sum(1 << bit for bit in range(64) if weights[bit] > 0)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def is_near_duplicate(a: int, b: int, threshold: int = 3) -> bool:
    return hamming(a, b) <= threshold
```

```python
# src/invest_agent/news/models.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_news_canonical.py -v`
Expected: PASS (8 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/news/ tests/test_news_canonical.py
git commit -m "feat: modelo de notícia, URL canônica e SimHash para dedupe"
```

---

### Task 2: Parser RSS/Atom em stdlib + registro de feeds

**Files:**
- Create: `src/invest_agent/news/rss.py`
- Test: `tests/test_rss.py`

**Interfaces:**
- Consumes: `make_news_item`, `NewsItem` (Task 1).
- Produces: `parse_feed(xml_bytes: bytes, source: str, now: datetime) -> list[NewsItem]` (RSS 2.0 e Atom; entradas sem título ou link são puladas; `published_at=None` quando ausente/imparseável); `FEEDS: dict[str, str]` (infomoney, valor, moneytimes, coindesk, cointelegraph — URLs da spec §4.1); `google_news_feed(query: str) -> str` (URL RSS do Google News pt-BR com `when:1d`). Task 6 consome estes nomes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rss.py
from datetime import datetime, timezone

from invest_agent.news.rss import FEEDS, google_news_feed, parse_feed

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>Bitcoin sobe</title>
<link>https://ex.com/a?utm_source=rss</link>
<pubDate>Tue, 09 Sep 2026 10:30:00 GMT</pubDate>
<description>Alta de 5%</description></item>
<item><title>Sem data</title><link>https://ex.com/b</link></item>
<item><title>Sem link</title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>
<entry><title>Ethereum cai</title>
<link href="https://ex.com/c"/>
<published>2026-09-09T08:00:00Z</published>
<summary>Queda</summary></entry>
</feed>"""


def test_parse_rss_20():
    items = parse_feed(RSS, "coindesk", NOW)
    assert len(items) == 2  # o sem link é pulado
    first = items[0]
    assert first.title == "Bitcoin sobe"
    assert first.url == "https://ex.com/a"  # utm removido via make_news_item
    assert first.published_at == datetime(2026, 9, 9, 10, 30,
                                          tzinfo=timezone.utc)
    assert first.ingested_at == NOW and first.source == "coindesk"
    assert first.summary == "Alta de 5%"
    assert items[1].published_at is None


def test_parse_atom():
    items = parse_feed(ATOM, "valor", NOW)
    assert len(items) == 1
    assert items[0].title == "Ethereum cai"
    assert items[0].url == "https://ex.com/c"
    assert items[0].published_at == datetime(2026, 9, 9, 8, 0,
                                             tzinfo=timezone.utc)


def test_parse_xml_invalido_retorna_vazio():
    assert parse_feed(b"nao e xml", "x", NOW) == []


def test_feeds_da_spec_presentes():
    assert {"infomoney", "valor", "moneytimes", "coindesk",
            "cointelegraph"} <= set(FEEDS)
    assert FEEDS["valor"].startswith("https://pox.globo.com/rss/valor")


def test_google_news_feed_pt_br_com_janela():
    url = google_news_feed("bitcoin")
    assert "news.google.com/rss/search" in url
    assert "bitcoin" in url and "when%3A1d" in url or "when:1d" in url
    assert "hl=pt-BR" in url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_rss.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/news/rss.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_rss.py -v`
Expected: PASS (5 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/news/rss.py tests/test_rss.py
git commit -m "feat: parser RSS/Atom em stdlib + feeds da spec e Google News"
```

---

### Task 3: Triagem por keyword (antes do LLM)

**Files:**
- Create: `src/invest_agent/news/triage.py`
- Test: `tests/test_triage.py`

**Interfaces:**
- Consumes: `NewsItem` (Task 1).
- Produces: `ASSET_KEYWORDS: dict[str, tuple[str, ...]]` (símbolo → palavras, ao menos BTCUSDT/ETHUSDT/SOLUSDT/BNBUSDT/XRPUSDT); `MACRO_KEYWORDS: tuple[str, ...]` (fed, juros, selic, sec, etf, regulament…); `match_assets(text: str) -> tuple[str, ...]` (símbolos em ordem alfabética); `triage(items: list[NewsItem]) -> list[tuple[NewsItem, tuple[str, ...]]]` (mantém item se casa ≥1 ativo OU ≥1 keyword macro; descarta o resto — "triagem por keyword antes do LLM", spec §4.1). Task 6 consome estes nomes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_triage.py
from datetime import datetime, timezone

from invest_agent.news.models import make_news_item
from invest_agent.news.triage import (
    ASSET_KEYWORDS, MACRO_KEYWORDS, match_assets, triage,
)

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _item(title: str, summary: str = "") -> object:
    return make_news_item("t", title, f"https://x.com/{abs(hash(title))}",
                          None, NOW, summary)


def test_match_assets_por_nome_e_ticker():
    assert match_assets("Bitcoin dispara com ETF") == ("BTCUSDT",)
    assert match_assets("alta do btc e do ethereum") == ("BTCUSDT", "ETHUSDT")
    assert match_assets("Vale paga dividendos") == ()


def test_match_e_case_insensitive_e_por_palavra_inteira():
    assert match_assets("SOLANA em alta") == ("SOLUSDT",)
    # "eth" dentro de outra palavra não casa (ex.: "methanol")
    assert match_assets("preço do methanol sobe") == ()


def test_triage_mantem_ativo_e_macro_descarta_resto():
    a = _item("Bitcoin sobe 5%")
    b = _item("Fed corta juros")            # macro, sem ativo
    c = _item("Novela das nove estreia")    # descartado
    kept = triage([a, b, c])
    assert [(i.title, assets) for i, assets in kept] == [
        ("Bitcoin sobe 5%", ("BTCUSDT",)),
        ("Fed corta juros", ()),
    ]


def test_triage_usa_summary_tambem():
    item = _item("Mercados hoje", "análise do ethereum e do mercado")
    (kept,) = triage([item])
    assert kept[1] == ("ETHUSDT",)


def test_keywords_minimas_presentes():
    assert {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"} <= set(
        ASSET_KEYWORDS)
    assert "selic" in MACRO_KEYWORDS and "fed" in MACRO_KEYWORDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_triage.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/news/triage.py
"""Triagem por keyword ANTES de qualquer LLM (spec §4.1) — barata,
determinística, e o que ela descarta nunca gasta token. O enriquecimento
LLM (resumo/sentimento/materialidade) é da Fase 2."""
from __future__ import annotations

import re

from .models import NewsItem

ASSET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "BTCUSDT": ("bitcoin", "btc"),
    "ETHUSDT": ("ethereum", "eth", "ether"),
    "SOLUSDT": ("solana", "sol"),
    "BNBUSDT": ("bnb", "binance coin"),
    "XRPUSDT": ("xrp", "ripple"),
}

MACRO_KEYWORDS: tuple[str, ...] = (
    "fed", "juros", "selic", "copom", "inflação", "cpi", "sec", "etf",
    "regulament", "banco central", "bcb", "halving", "cvm",
)


def _has_word(word: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(word)}", text) is not None


def match_assets(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    matched = {symbol
               for symbol, words in ASSET_KEYWORDS.items()
               if any(_has_word(w, lowered) for w in words)}
    return tuple(sorted(matched))


def triage(items: list[NewsItem]) -> list[tuple[NewsItem, tuple[str, ...]]]:
    kept: list[tuple[NewsItem, tuple[str, ...]]] = []
    for item in items:
        text = f"{item.title} {item.summary}"
        assets = match_assets(text)
        is_macro = any(_has_word(w, text.lower()) for w in MACRO_KEYWORDS)
        if assets or is_macro:
            kept.append((item, assets))
    return kept
```

**Nota:** `_has_word` usa `\b` só no INÍCIO da palavra de propósito: "regulament" deve casar "regulamentação"/"regulamento". Para tickers curtos ("eth", "sol", "btc"), o `\b` inicial impede casar dentro de outra palavra ("methanol" não casa "eth" pois não há fronteira antes do "eth"). "girassol" não casa "sol" pelo mesmo motivo.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_triage.py -v`
Expected: PASS (5 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/news/triage.py tests/test_triage.py
git commit -m "feat: triagem de notícias por keyword (ativos + macro) pré-LLM"
```

---

### Task 4: SQLite — notícias, macro e decision log append-only

**Files:**
- Create: `src/invest_agent/storage/__init__.py`
- Create: `src/invest_agent/storage/sqlite_store.py`
- Test: `tests/test_sqlite_store.py`

**Interfaces:**
- Consumes: `NewsItem` (Task 1), `simhash64` (Task 1).
- Produces: `MacroPoint` (dataclass frozen: `series: str`, `date: date`, `value: float`); `DecisionRecord` (dataclass frozen: `decision_id: str`, `ts: datetime`, `inputs_hash: str`, `snapshot_json: str`, `proposal_json: str`, `verdict_json: str`, `order_json: str | None = None`, `fills_json: str | None = None`, `api_cost_usd: float = 0.0`); classe `SqliteStore(path: Path)` com: `insert_news(items: list[tuple[NewsItem, tuple[str, ...]]]) -> int` (nº de novas; ids repetidos ignorados), `known_urls() -> set[str]`, `known_simhashes() -> list[int]`, `upsert_macro(point: MacroPoint) -> None`, `latest_macro(series: str) -> MacroPoint | None`, `append_decision(record: DecisionRecord) -> None` (id repetido → `sqlite3.IntegrityError`), `read_decisions() -> list[DecisionRecord]`, `close()`. Colunas de enriquecimento LLM (`summary_llm`, `sentiment`, `materiality`) já existem em `news`, NULL até a Fase 2. Task 6 consome estes nomes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sqlite_store.py
import sqlite3
from datetime import date, datetime, timezone

import pytest

from invest_agent.news.models import make_news_item
from invest_agent.storage.sqlite_store import (
    DecisionRecord, MacroPoint, SqliteStore,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _item(title: str, url: str):
    return make_news_item("src", title, url, NOW, NOW, "resumo")


def test_insert_news_dedupe_por_id(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    item = _item("t1", "https://x.com/1")
    assert store.insert_news([(item, ("BTCUSDT",))]) == 1
    assert store.insert_news([(item, ("BTCUSDT",))]) == 0  # mesmo id
    assert store.known_urls() == {"https://x.com/1"}
    assert len(store.known_simhashes()) == 1
    store.close()


def test_macro_upsert_e_leitura(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_macro(MacroPoint("selic", date(2026, 9, 9), 15.0))
    store.upsert_macro(MacroPoint("selic", date(2026, 9, 9), 14.75))  # upsert
    latest = store.latest_macro("selic")
    assert latest == MacroPoint("selic", date(2026, 9, 9), 14.75)
    assert store.latest_macro("fng") is None
    store.close()


def _record(decision_id: str = "d1") -> DecisionRecord:
    return DecisionRecord(decision_id=decision_id, ts=NOW,
                          inputs_hash="abc", snapshot_json="{}",
                          proposal_json="{}", verdict_json="{}")


def test_decision_log_append_e_leitura(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_record())
    (lido,) = store.read_decisions()
    assert lido == _record()
    store.close()


def test_decision_log_id_duplicado_aborta(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_record())
    with pytest.raises(sqlite3.IntegrityError):
        store.append_decision(_record())
    store.close()


def test_decision_log_update_e_delete_abortam(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_record())
    raw = sqlite3.connect(tmp_path / "a.db")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute("UPDATE decision_log SET inputs_hash='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw.execute("DELETE FROM decision_log")
    raw.close()
    store.close()


def test_reabrir_o_banco_preserva_dados(tmp_path):
    path = tmp_path / "a.db"
    store = SqliteStore(path)
    store.insert_news([(_item("t", "https://x.com/2"), ())])
    store.close()
    store2 = SqliteStore(path)
    assert store2.known_urls() == {"https://x.com/2"}
    store2.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sqlite_store.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`src/invest_agent/storage/__init__.py`: vazio.

```python
# src/invest_agent/storage/sqlite_store.py
"""SQLite (stdlib) — spec §4.2: notícias (com colunas de enriquecimento LLM
reservadas para a Fase 2), macro diário e o log de decisões APPEND-ONLY
(triggers abortam UPDATE/DELETE; é a base de auditoria, debug e IR)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ..news.canonical import simhash64
from ..news.models import NewsItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    ingested_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    simhash INTEGER NOT NULL,
    assets TEXT NOT NULL,
    summary_llm TEXT,
    sentiment TEXT,
    materiality INTEGER
);
CREATE TABLE IF NOT EXISTS macro (
    series TEXT NOT NULL,
    date TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (series, date)
);
CREATE TABLE IF NOT EXISTS decision_log (
    decision_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    inputs_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    verdict_json TEXT NOT NULL,
    order_json TEXT,
    fills_json TEXT,
    api_cost_usd REAL NOT NULL DEFAULT 0.0
);
CREATE TRIGGER IF NOT EXISTS decision_log_no_update
BEFORE UPDATE ON decision_log
BEGIN SELECT RAISE(ABORT, 'decision_log é append-only'); END;
CREATE TRIGGER IF NOT EXISTS decision_log_no_delete
BEFORE DELETE ON decision_log
BEGIN SELECT RAISE(ABORT, 'decision_log é append-only'); END;
"""


@dataclass(frozen=True, slots=True)
class MacroPoint:
    series: str
    date: date
    value: float


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_id: str
    ts: datetime
    inputs_hash: str
    snapshot_json: str
    proposal_json: str
    verdict_json: str
    order_json: str | None = None
    fills_json: str | None = None
    api_cost_usd: float = 0.0


class SqliteStore:
    def __init__(self, path: Path):
        self._con = sqlite3.connect(path)
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    # --- notícias ---

    def insert_news(self,
                    items: list[tuple[NewsItem, tuple[str, ...]]]) -> int:
        added = 0
        for item, assets in items:
            cur = self._con.execute(
                "INSERT OR IGNORE INTO news (id, source, title, url,"
                " published_at, ingested_at, summary, simhash, assets)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (item.id, item.source, item.title, item.url,
                 item.published_at.isoformat() if item.published_at else None,
                 item.ingested_at.isoformat(), item.summary,
                 _to_signed(simhash64(item.title)), ",".join(assets)))
            added += cur.rowcount
        self._con.commit()
        return added

    def known_urls(self) -> set[str]:
        rows = self._con.execute("SELECT url FROM news").fetchall()
        return {url for (url,) in rows}

    def known_simhashes(self) -> list[int]:
        rows = self._con.execute("SELECT simhash FROM news").fetchall()
        return [_from_signed(value) for (value,) in rows]

    # --- macro ---

    def upsert_macro(self, point: MacroPoint) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO macro (series, date, value)"
            " VALUES (?,?,?)",
            (point.series, point.date.isoformat(), point.value))
        self._con.commit()

    def latest_macro(self, series: str) -> MacroPoint | None:
        row = self._con.execute(
            "SELECT series, date, value FROM macro WHERE series=?"
            " ORDER BY date DESC LIMIT 1", (series,)).fetchone()
        if row is None:
            return None
        return MacroPoint(row[0], date.fromisoformat(row[1]), row[2])

    # --- decision log (append-only) ---

    def append_decision(self, record: DecisionRecord) -> None:
        self._con.execute(
            "INSERT INTO decision_log (decision_id, ts, inputs_hash,"
            " snapshot_json, proposal_json, verdict_json, order_json,"
            " fills_json, api_cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
            (record.decision_id, record.ts.isoformat(), record.inputs_hash,
             record.snapshot_json, record.proposal_json, record.verdict_json,
             record.order_json, record.fills_json, record.api_cost_usd))
        self._con.commit()

    def read_decisions(self) -> list[DecisionRecord]:
        rows = self._con.execute(
            "SELECT decision_id, ts, inputs_hash, snapshot_json,"
            " proposal_json, verdict_json, order_json, fills_json,"
            " api_cost_usd FROM decision_log ORDER BY ts").fetchall()
        return [DecisionRecord(decision_id=r[0],
                               ts=datetime.fromisoformat(r[1]),
                               inputs_hash=r[2], snapshot_json=r[3],
                               proposal_json=r[4], verdict_json=r[5],
                               order_json=r[6], fills_json=r[7],
                               api_cost_usd=r[8])
                for r in rows]


def _to_signed(value: int) -> int:
    """SQLite INTEGER é assinado de 64 bits; SimHash é sem sinal."""
    return value - (1 << 64) if value >= (1 << 63) else value


def _from_signed(value: int) -> int:
    return value + (1 << 64) if value < 0 else value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sqlite_store.py -v`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/storage/ tests/test_sqlite_store.py
git commit -m "feat: SQLite com notícias, macro e decision log append-only"
```

---

### Task 5: Fetchers macro — Fear & Greed e BCB SGS

**Files:**
- Create: `src/invest_agent/macro/__init__.py`
- Create: `src/invest_agent/macro/fetchers.py`
- Test: `tests/test_macro_fetchers.py`

**Interfaces:**
- Consumes: `MacroPoint` (Task 4).
- Produces: `fetch_fear_greed(transport) -> MacroPoint` (série "fng", data do timestamp unix, valor 0-100); `fetch_bcb_sgs(series_id: int, series_name: str, transport) -> MacroPoint` (último ponto; data dd/mm/aaaa; valor com vírgula OU ponto decimal); constantes `SGS_SELIC = 432`, `SGS_CAMBIO = 1` (spec §4.1). Task 6 consome estes nomes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macro_fetchers.py
from datetime import date

from invest_agent.macro.fetchers import (
    SGS_CAMBIO, SGS_SELIC, fetch_bcb_sgs, fetch_fear_greed,
)
from invest_agent.storage.sqlite_store import MacroPoint

FNG = b'{"data":[{"value":"34","value_classification":"Fear","timestamp":"1788998400"}]}'
SGS = b'[{"data":"09/09/2026","valor":"15,00"}]'
SGS_PONTO = b'[{"data":"09/09/2026","valor":"5.4321"}]'


def test_fetch_fear_greed():
    urls = []

    def transport(url):
        urls.append(url)
        return FNG

    point = fetch_fear_greed(transport)
    assert point == MacroPoint("fng", date(2026, 9, 9), 34.0)
    assert "alternative.me/fng" in urls[0]


def test_fetch_bcb_sgs_virgula_decimal():
    urls = []

    def transport(url):
        urls.append(url)
        return SGS

    point = fetch_bcb_sgs(SGS_SELIC, "selic", transport)
    assert point == MacroPoint("selic", date(2026, 9, 9), 15.0)
    assert "bcdata.sgs.432" in urls[0] and "ultimos/1" in urls[0]


def test_fetch_bcb_sgs_ponto_decimal():
    point = fetch_bcb_sgs(SGS_CAMBIO, "cambio", lambda u: SGS_PONTO)
    assert point == MacroPoint("cambio", date(2026, 9, 9), 5.4321)


def test_constantes_da_spec():
    assert SGS_SELIC == 432 and SGS_CAMBIO == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_macro_fetchers.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`src/invest_agent/macro/__init__.py`: vazio.

```python
# src/invest_agent/macro/fetchers.py
"""Fetchers macro da spec §4.1 (1×/dia): Fear & Greed (alternative.me) e
BCB SGS (Selic 432, câmbio 1). Transport injetável — nenhum teste toca a
rede; o timestamp do próprio dado (não o relógio local) define a data."""
from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timezone
from typing import Callable

from ..storage.sqlite_store import MacroPoint

FNG_URL = "https://api.alternative.me/fng/?limit=1"
SGS_URL = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_id}"
           "/dados/ultimos/1?formato=json")

SGS_SELIC = 432
SGS_CAMBIO = 1


def _default_transport(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def fetch_fear_greed(
        transport: Callable[[str], bytes] | None = None) -> MacroPoint:
    transport = transport or _default_transport
    payload = json.loads(transport(FNG_URL))
    entry = payload["data"][0]
    when = datetime.fromtimestamp(int(entry["timestamp"]),
                                  tz=timezone.utc).date()
    return MacroPoint("fng", when, float(entry["value"]))


def fetch_bcb_sgs(series_id: int, series_name: str,
                  transport: Callable[[str], bytes] | None = None
                  ) -> MacroPoint:
    transport = transport or _default_transport
    payload = json.loads(transport(SGS_URL.format(series_id=series_id)))
    entry = payload[-1]
    day, month, year = entry["data"].split("/")
    value = float(entry["valor"].replace(",", "."))
    return MacroPoint(series_name, date(int(year), int(month), int(day)),
                      value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_macro_fetchers.py -v`
Expected: PASS (4 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/macro/ tests/test_macro_fetchers.py
git commit -m "feat: fetchers macro — Fear & Greed e BCB SGS (Selic, câmbio)"
```

---

### Task 6: Pipeline de ingestão de notícias + CLI

**Files:**
- Create: `src/invest_agent/news/ingest.py`
- Test: `tests/test_news_ingest.py`
- Modify: `README.md` (seção de uso)

**Interfaces:**
- Consumes: `FEEDS`/`parse_feed`/`google_news_feed` (Task 2), `triage` (Task 3), `SqliteStore`/`MacroPoint` (Task 4), fetchers (Task 5), `simhash64`/`is_near_duplicate` (Task 1).
- Produces: `ingest_news(store: SqliteStore, feeds: dict[str, str], transport, now: datetime) -> dict` (contadores: `fetched`, `kept`, `inserted`, `dup_url`, `dup_simhash`, `feeds_failed`) e `main(argv)` executável via `python3 -m invest_agent.news.ingest` (flags: `--db data/agent.db`, `--macro` para também buscar fng/selic/câmbio).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_news_ingest.py
from datetime import datetime, timezone

from invest_agent.news.ingest import ingest_news
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

FEED_A = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Bitcoin sobe forte hoje</title>
<link>https://ex.com/btc?utm_source=a</link></item>
<item><title>Novela estreia</title><link>https://ex.com/tv</link></item>
</channel></rss>"""

# mesma notícia com URL de tracking diferente (dup por URL canônica)
FEED_B = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Bitcoin sobe forte hoje</title>
<link>https://ex.com/btc?utm_source=b</link></item>
</channel></rss>"""

# título quase idêntico em OUTRA URL (dup por SimHash)
FEED_C = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Bitcoin sobe forte hoje!</title>
<link>https://outro.com/mirror</link></item>
</channel></rss>"""


def _transport(mapping):
    def transport(url):
        for key, payload in mapping.items():
            if key in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"URL inesperada: {url}")
    return transport


def test_ingest_dedupe_url_e_simhash_e_triagem(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    feeds = {"a": "https://feeds.a/rss", "b": "https://feeds.b/rss",
             "c": "https://feeds.c/rss"}
    transport = _transport({"feeds.a": FEED_A, "feeds.b": FEED_B,
                            "feeds.c": FEED_C})
    stats = ingest_news(store, feeds, transport, NOW)
    # 4 entradas nos 3 feeds; "Novela" cai na triagem; dup URL e dup simhash
    assert stats["fetched"] == 4
    assert stats["kept"] == 3           # após triagem
    assert stats["inserted"] == 1       # só a primeira do Bitcoin entra
    assert stats["dup_url"] == 1
    assert stats["dup_simhash"] == 1
    assert store.known_urls() == {"https://ex.com/btc"}
    store.close()


def test_ingest_feed_que_falha_nao_derruba_o_resto(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    feeds = {"a": "https://feeds.a/rss", "x": "https://feeds.x/rss"}
    transport = _transport({"feeds.a": FEED_A,
                            "feeds.x": OSError("rede caiu")})
    stats = ingest_news(store, feeds, transport, NOW)
    assert stats["feeds_failed"] == 1
    assert stats["inserted"] == 1
    store.close()


def test_ingest_e_idempotente(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    feeds = {"a": "https://feeds.a/rss"}
    transport = _transport({"feeds.a": FEED_A})
    first = ingest_news(store, feeds, transport, NOW)
    second = ingest_news(store, feeds, transport, NOW)
    assert first["inserted"] == 1 and second["inserted"] == 0
    assert second["dup_url"] == 1
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_news_ingest.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/news/ingest.py
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
```

No `README.md`, acrescentar após a seção "📊 Backtest vs buy-and-hold (Fase 1)":

```markdown
## 📰 Ingestão de notícias e macro (Fase 1)

```bash
python3 -m invest_agent.news.ingest --macro
```

RSS (InfoMoney, Valor, MoneyTimes, CoinDesk, CoinTelegraph) → triagem por
keyword (só o que cita ativos da whitelist ou temas macro) → dedupe em dois
estágios (URL canônica, SimHash de título) → SQLite (`data/agent.db`) com
`published_at` ≠ `ingested_at` (anti look-ahead). `--macro` adiciona Fear &
Greed, Selic e câmbio (BCB SGS). Dedupe por embedding e enriquecimento LLM
ficam para a Fase 2.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_news_ingest.py -v`
Expected: PASS (3 testes). Suíte completa: `python3 -m pytest` — tudo verde.

- [ ] **Step 5: Smoke test manual (com rede; pode falhar por SSL local)**

Run: `python3 -m invest_agent.news.ingest --db /tmp/smoke-agent.db 2>/dev/null || echo "rede/SSL indisponível — ok, testes unitários são o gate"`
Expected: linha de resumo impressa, ou a mensagem de fallback.

- [ ] **Step 6: Commit**

```bash
git add src/invest_agent/news/ingest.py tests/test_news_ingest.py README.md
git commit -m "feat: pipeline de ingestão de notícias + CLI com macro"
```

---

## Self-review do plano (executada na escrita)

- **Cobertura da spec (escopo 1c):** RSS dos 5 veículos + Google News helper → T2; normalizar → dedupe (URL canônica → SimHash) → T1+T6 (embedding deferido, ruling); triagem por keyword antes do LLM → T3; `published_at ≠ ingested_at` → T1 (modelo) + T4 (colunas) + T6 (pipeline); F&G + BCB SGS 1×/dia → T5+T6 (`--macro`); SQLite estado+log com decision log append-only → T4. Enriquecimento LLM, embedding e índice vetorial: Fase 2 (rulings no cabeçalho).
- **Placeholders:** nenhum; todo step tem código completo.
- **Consistência de tipos:** `NewsItem(id, source, title, url, published_at, ingested_at, summary)` idêntico em T1/T2/T4; `make_news_item` (T1) usado em T2 e nos testes de T3/T4; `triage → list[tuple[NewsItem, tuple[str,...]]]` (T3) é exatamente o input de `insert_news` (T4) e o que `ingest_news` (T6) passa adiante; `MacroPoint(series, date, value)` idêntico em T4/T5/T6; `simhash64`/`is_near_duplicate` (T1) usados em T4 (persistência) e T6 (dedupe); contadores de `ingest_news` casam com os asserts dos 3 testes de T6 (4 fetched / 3 kept / 1 inserted / 1 dup_url / 1 dup_simhash conferidos à mão: FEED_A tem 2 itens (1 cai na triagem), FEED_B 1 (dup URL canônica), FEED_C 1 (dup SimHash — título difere só por "!", que o tokenizador `\w+` ignora → simhash idêntico, distância 0 ≤ 3)).
- **Risco conhecido:** `test_simhash_textos_diferentes_ficam_longe` depende da distribuição do blake2b — com 64 bits e textos sem palavras em comum, hamming > 10 é praticamente certo; se falhar, é legítimo investigar (não ajustar o threshold às cegas).
