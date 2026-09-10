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
