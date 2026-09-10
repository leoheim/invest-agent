from datetime import datetime, timedelta, timezone

from invest_agent.config import MODERADO
from invest_agent.models import OrderIntent
from invest_agent.orchestrator.cycle import CycleResult
from invest_agent.storage.sqlite_store import SqliteStore
from invest_agent.telegram.format import (
    build_status_text, format_breaker, format_cycle_result,
    format_hitl_request, format_material_news,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
ORDER = OrderIntent(symbol="BTCUSDT", side="BUY", qty=0.001,
                    limit_price=100_000.0, stop_loss_price=95_000.0,
                    client_order_id="ia-abc")


def test_format_cycle_result_executada():
    result = CycleResult("2026091012", "approved", [], True)
    texto = format_cycle_result(result, ORDER)
    assert "BTCUSDT" in texto and "BUY" in texto and "executada" in texto


def test_format_cycle_result_rejeitada_lista_motivos():
    result = CycleResult("2026091012", "rejected",
                         ["cooldown ativo", "spread alto"], False)
    texto = format_cycle_result(result, None)
    assert "cooldown ativo" in texto and "spread alto" in texto


def test_format_cycle_result_hold_e_curto():
    result = CycleResult("2026091012", "approved", [], False)
    assert len(format_cycle_result(result, None)) < 80


def test_format_hitl_request():
    texto = format_hitl_request("d1", ORDER, ["acima do limiar"])
    assert "d1" in texto and "BTCUSDT" in texto and "acima do limiar" in texto


def test_format_breaker_e_news():
    assert "DAY" in format_breaker("DAY")
    news = format_material_news("ETF aprovado", "coindesk", "BTCUSDT", 5)
    assert "ETF aprovado" in news and "5" in news


def test_build_status_text(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_position("BTCUSDT", 0.5, 90_000.0)
    store.add_api_cost(NOW.date(), 0.42)
    store.add_pending("d1", NOW, NOW + timedelta(minutes=10))
    texto = build_status_text(store, MODERADO, NOW)
    assert "BTCUSDT" in texto and "0.5" in texto
    assert "0.42" in texto and "moderado" in texto
    assert "d1" in texto
    store.close()
