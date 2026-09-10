from datetime import datetime, timezone

from invest_agent.breakers import HaltLevel
from invest_agent.orchestrator.state import (
    active_halt, build_portfolio, ensure_marks, record_halt_if_needed,
)
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)  # quinta-feira


def test_build_portfolio_mark_to_market(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_position("BTCUSDT", 0.5, 90_000.0)   # avg 90k
    store.upsert_position("ETHUSDT", 2.0, 4_000.0)
    balances = {"USDT": 1_000.0, "BTC": 0.5, "ETH": 2.0}
    prices = {"BTCUSDT": 100_000.0, "ETHUSDT": 3_000.0}
    pf = build_portfolio(store, balances, prices, NOW)
    assert pf.cash == 1_000.0
    # equity marcado a MERCADO: 1000 + 0.5*100k + 2*3k = 57_000
    assert pf.equity == 57_000.0
    assert pf.positions["BTCUSDT"].qty == 0.5
    assert pf.positions["BTCUSDT"].avg_price == 90_000.0
    store.close()


def test_build_portfolio_balance_zerado_remove_posicao(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_position("BTCUSDT", 0.5, 90_000.0)
    pf = build_portfolio(store, {"USDT": 100.0}, {"BTCUSDT": 100_000.0}, NOW)
    assert pf.positions == {} and pf.equity == 100.0
    assert store.get_positions() == {}  # limpou a tabela
    store.close()


def test_build_portfolio_qty_do_balance_vence(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_position("BTCUSDT", 0.5, 90_000.0)
    balances = {"USDT": 0.0, "BTC": 0.3}  # exchange diz 0.3
    pf = build_portfolio(store, balances, {"BTCUSDT": 100_000.0}, NOW)
    assert pf.positions["BTCUSDT"].qty == 0.3
    store.close()


def test_ensure_marks_cria_e_rola(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    marks = ensure_marks(store, 10_000.0, NOW)
    assert marks.day_open == marks.week_open == marks.month_open == 10_000.0
    # mesmo dia: não rola
    marks2 = ensure_marks(store, 9_000.0, NOW.replace(hour=18))
    assert marks2.day_open == 10_000.0
    # dia seguinte (sexta): rola só o day
    marks3 = ensure_marks(store, 9_000.0, NOW.replace(day=11))
    assert marks3.day_open == 9_000.0 and marks3.week_open == 10_000.0
    # segunda seguinte (14/09): rola day e week; mês não
    seg = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)
    marks4 = ensure_marks(store, 8_000.0, seg)
    assert marks4.week_open == 8_000.0 and marks4.month_open == 10_000.0
    # outubro: rola tudo
    out = datetime(2026, 10, 1, 0, 5, tzinfo=timezone.utc)
    marks5 = ensure_marks(store, 7_000.0, out)
    assert marks5.month_open == 7_000.0
    store.close()


def test_halt_day_expira_no_dia_seguinte(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.DAY, NOW)
    assert active_halt(store, NOW.replace(hour=23)) is HaltLevel.DAY
    assert active_halt(store, NOW.replace(day=11)) is HaltLevel.NONE
    store.close()


def test_halt_week_expira_na_segunda_seguinte(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.WEEK, NOW)  # quinta 10/09
    dom = datetime(2026, 9, 13, 23, 0, tzinfo=timezone.utc)
    seg = datetime(2026, 9, 14, 0, 5, tzinfo=timezone.utc)
    assert active_halt(store, dom) is HaltLevel.WEEK
    assert active_halt(store, seg) is HaltLevel.NONE
    store.close()


def test_halt_month_so_sai_com_release_manual(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.MONTH, NOW)
    prox_ano = datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert active_halt(store, prox_ano) is HaltLevel.MONTH
    store.release_halt()
    assert active_halt(store, prox_ano) is HaltLevel.NONE
    store.close()


def test_halt_mais_severo_sobrescreve_mas_menos_severo_nao(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.DAY, NOW)
    record_halt_if_needed(store, HaltLevel.MONTH, NOW)
    assert active_halt(store, NOW) is HaltLevel.MONTH
    record_halt_if_needed(store, HaltLevel.DAY, NOW)  # ignorado
    assert active_halt(store, NOW) is HaltLevel.MONTH
    record_halt_if_needed(store, HaltLevel.NONE, NOW)  # no-op
    assert active_halt(store, NOW) is HaltLevel.MONTH
    store.close()


def test_halt_day_expirado_reengata_no_dia_seguinte(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.DAY, NOW)          # quinta
    amanha = NOW.replace(day=11)
    assert active_halt(store, amanha) is HaltLevel.NONE       # expirou
    record_halt_if_needed(store, HaltLevel.DAY, amanha)       # novo gatilho
    assert active_halt(store, amanha) is HaltLevel.DAY        # re-engatou
    store.close()
