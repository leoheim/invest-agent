import pytest
from invest_agent.models import (
    Action, Proposal, Position, Verdict, VerdictStatus,
)


def test_proposal_valida_conviction():
    p = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.7,
                 rationale="teste", cycle_id="c1")
    assert p.conviction == 0.7


def test_proposal_rejeita_conviction_fora_da_faixa():
    with pytest.raises(ValueError):
        Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=1.5,
                 rationale="teste", cycle_id="c1")


def test_position_notional_usa_preco_corrente():
    pos = Position(symbol="ETHUSDT", qty=2.0, avg_price=2000.0)
    assert pos.notional(price=2500.0) == 5000.0


def test_verdict_rejeitado_carrega_motivos():
    v = Verdict(status=VerdictStatus.REJECTED,
                reasons=["fora da whitelist"], order=None)
    assert v.status is VerdictStatus.REJECTED
    assert v.order is None
