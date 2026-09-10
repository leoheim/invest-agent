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
