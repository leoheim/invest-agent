from datetime import datetime, timezone

import pytest

from invest_agent.breakers import EquityMarks
from invest_agent.config import MODERADO
from invest_agent.engine import RulesEngine
from invest_agent.killswitch import KillSwitch
from invest_agent.models import (
    Action, MarketSnapshot, PortfolioState, Position, Proposal, VerdictStatus,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
MARKS = EquityMarks(day_open=1000.0, week_open=1000.0, month_open=1000.0)
WL = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})


@pytest.fixture
def engine(tmp_path):
    return RulesEngine(profile=MODERADO, whitelist=WL,
                       kill_switch=KillSwitch(tmp_path / "KILL"))


def _mkt(symbol="SOLUSDT", price=50.0):
    return MarketSnapshot(symbol=symbol, last_price=price,
                          best_bid=price * 0.999, best_ask=price * 1.001,
                          candle_age_seconds=60.0, quote_volume_24h=1e8)


def _pf(**kw):
    base = dict(equity=10_000.0, cash=10_000.0, positions={},
                orders_today=0, last_order_at={})
    base.update(kw)
    return PortfolioState(**base)


def _prop(symbol="SOLUSDT", action=Action.BUY, conviction=0.10):
    return Proposal(symbol=symbol, action=action, conviction=conviction,
                    rationale="teste", cycle_id="ciclo-1")


def test_hold_aprovado_sem_ordem(engine):
    v = engine.evaluate(_prop(action=Action.HOLD), _pf(), _mkt(), MARKS, NOW)
    assert v.status is VerdictStatus.APPROVED and v.order is None


def test_compra_aprovada_tem_stop_loss_e_id_deterministico(engine):
    # conviction 0.10 => 10000*0.10*0.10 = 100 USDT = 1% < HITL 2%
    v = engine.evaluate(_prop(), _pf(), _mkt(), MARKS, NOW)
    assert v.status is VerdictStatus.APPROVED
    assert v.order is not None
    assert v.order.stop_loss_price == pytest.approx(
        v.order.limit_price * (1 - MODERADO.stop_loss_pct))
    v2 = engine.evaluate(_prop(), _pf(), _mkt(), MARKS, NOW)
    assert v.order.client_order_id == v2.order.client_order_id
    assert v.order.client_order_id.startswith("ia-")


def test_fora_da_whitelist_rejeita(engine):
    v = engine.evaluate(_prop(symbol="SCAMUSDT"), _pf(),
                        _mkt(symbol="SCAMUSDT"), MARKS, NOW)
    assert v.status is VerdictStatus.REJECTED
    assert any("whitelist" in r for r in v.reasons)


def test_kill_switch_bloqueia_tudo(engine):
    engine.kill_switch.activate("teste")
    v = engine.evaluate(_prop(), _pf(), _mkt(), MARKS, NOW)
    assert v.status is VerdictStatus.REJECTED
    assert any("kill switch" in r for r in v.reasons)


def test_breaker_bloqueia(engine):
    marks = EquityMarks(day_open=11_000.0, week_open=11_000.0,
                        month_open=11_000.0)  # equity 10k = -9% no dia
    v = engine.evaluate(_prop(), _pf(), _mkt(), marks, NOW)
    assert v.status is VerdictStatus.REJECTED
    assert any("circuit breaker" in r for r in v.reasons)


def test_ordem_grande_pede_aprovacao_humana(engine):
    # conviction 1.0 => alvo 1000 USDT = 10% > HITL 2%
    v = engine.evaluate(_prop(conviction=1.0), _pf(), _mkt(), MARKS, NOW)
    assert v.status is VerdictStatus.NEEDS_APPROVAL
    assert v.order is not None


def test_venda_sem_posicao_rejeita(engine):
    v = engine.evaluate(_prop(action=Action.SELL), _pf(), _mkt(), MARKS, NOW)
    assert v.status is VerdictStatus.REJECTED
    assert any("sem posição" in r for r in v.reasons)


def test_close_vende_a_posicao_inteira(engine):
    pf = _pf(positions={"SOLUSDT": Position("SOLUSDT", qty=3.0, avg_price=40.0)})
    v = engine.evaluate(_prop(action=Action.CLOSE, conviction=1.0),
                        pf, _mkt(), MARKS, NOW)
    # 3 * ~50 = ~150 USDT = 1.5% < HITL => aprovado direto
    assert v.status is VerdictStatus.APPROVED
    assert v.order.side == "SELL" and v.order.qty == 3.0
    assert v.order.stop_loss_price is None


def test_close_fora_da_whitelist_ainda_permitido(engine):
    # Regressão: posição existente em símbolo fora da whitelist deve ser fechável.
    # A whitelist governa entradas, não saídas.
    pf = _pf(positions={"SCAMUSDT": Position("SCAMUSDT", qty=2.0, avg_price=40.0)})
    v = engine.evaluate(_prop(symbol="SCAMUSDT", action=Action.CLOSE, conviction=1.0),
                        pf, _mkt(symbol="SCAMUSDT"), MARKS, NOW)
    # 2 * ~50 = ~100 USDT = 1% < HITL => aprovado direto
    assert v.status is VerdictStatus.APPROVED
    assert v.order.side == "SELL" and v.order.qty == 2.0
    assert not any("whitelist" in r for r in v.reasons)
