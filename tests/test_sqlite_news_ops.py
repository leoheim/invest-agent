from datetime import datetime, timedelta, timezone

from invest_agent.news.models import make_news_item
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _insert(store, title, url, hours_ago=1, assets=("BTCUSDT",)):
    item = make_news_item("src", title, url,
                          NOW - timedelta(hours=hours_ago),
                          NOW - timedelta(hours=hours_ago), "resumo")
    store.insert_news([(item, assets)])
    return item


def test_unenriched_e_set_enrichment(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    velho = _insert(store, "t-velho", "https://x.com/1", hours_ago=5)
    novo = _insert(store, "t-novo", "https://x.com/2", hours_ago=1)
    pend = store.unenriched_news()
    assert [p[0] for p in pend] == [velho.id, novo.id]  # antigas primeiro
    assert pend[0][1] == "t-velho" and pend[0][2] == "resumo"
    store.set_news_enrichment(velho.id, "resumo llm", "negativo", 4)
    assert [p[0] for p in store.unenriched_news()] == [novo.id]
    store.close()


def test_unenriched_respeita_limit(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    for i in range(5):
        _insert(store, f"t{i}", f"https://x.com/{i}", hours_ago=5 - i)
    assert len(store.unenriched_news(limit=3)) == 3
    store.close()


def test_recent_news_filtra_e_ordena(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    recente = _insert(store, "recente", "https://x.com/1", hours_ago=2)
    _insert(store, "antiga", "https://x.com/2", hours_ago=48)
    store.set_news_enrichment(recente.id, "s", "positivo", 3)
    out = store.recent_news(since=NOW - timedelta(hours=24))
    assert len(out) == 1
    title, source, assets, published_at, sentiment, materiality = out[0]
    assert title == "recente" and source == "src"
    assert assets == ("BTCUSDT",)
    assert sentiment == "positivo" and materiality == 3
    store.close()


def test_indices_criados(tmp_path):
    import sqlite3
    store = SqliteStore(tmp_path / "a.db")
    store.close()
    con = sqlite3.connect(tmp_path / "a.db")
    nomes = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    con.close()
    assert {"idx_news_url", "idx_news_simhash", "idx_decision_ts"} <= nomes
