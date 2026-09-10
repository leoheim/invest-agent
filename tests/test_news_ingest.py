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
