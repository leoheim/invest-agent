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
