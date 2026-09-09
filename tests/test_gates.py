from datetime import datetime, timedelta, timezone

from invest_agent.config import MODERADO
from invest_agent.gates import check_frequency, check_market_quality
from invest_agent.models import MarketSnapshot, PortfolioState

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _mkt(**kw):
    base = dict(symbol="BTCUSDT", last_price=100_000.0, best_bid=99_990.0,
                best_ask=100_010.0, candle_age_seconds=60.0,
                quote_volume_24h=1e9)
    base.update(kw)
    return MarketSnapshot(**base)


def test_mercado_saudavel_passa():
    assert check_market_quality(_mkt(), MODERADO) == []


def test_dado_velho_rejeita():
    viol = check_market_quality(_mkt(candle_age_seconds=999.0), MODERADO)
    assert any("dado de mercado velho" in v for v in viol)


def test_spread_largo_rejeita():
    viol = check_market_quality(
        _mkt(best_bid=99_000.0, best_ask=101_000.0), MODERADO)
    assert any("spread" in v for v in viol)


def test_book_invertido_rejeita():
    viol = check_market_quality(
        _mkt(best_bid=100_020.0, best_ask=100_010.0), MODERADO)
    assert any("book" in v for v in viol)


def test_limite_diario_de_ordens():
    pf = PortfolioState(equity=1000.0, cash=1000.0, orders_today=4)
    viol = check_frequency("BTCUSDT", pf, MODERADO, NOW)
    assert any("ordens no dia" in v for v in viol)


def test_cooldown_por_ativo():
    pf = PortfolioState(equity=1000.0, cash=1000.0, orders_today=1,
                        last_order_at={"BTCUSDT": NOW - timedelta(hours=1)})
    viol = check_frequency("BTCUSDT", pf, MODERADO, NOW)
    assert any("cooldown" in v for v in viol)
    # outro ativo não está em cooldown
    assert check_frequency("ETHUSDT", pf, MODERADO, NOW) == []
