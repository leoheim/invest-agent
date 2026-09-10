# tests/test_hitl.py
from datetime import datetime, timedelta, timezone

from invest_agent.breakers import HaltLevel
from invest_agent.models import (Action, OrderIntent, PortfolioState,
                                 Position, Proposal, Verdict, VerdictStatus)
from invest_agent.orchestrator.hitl import apply_hitl_overrides
from invest_agent.orchestrator.state import record_halt_if_needed
from invest_agent.storage.sqlite_store import DecisionRecord, SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
ORDER = OrderIntent(symbol="SOLUSDT", side="BUY", qty=1.0,
                    limit_price=100.0, stop_loss_price=95.0,
                    client_order_id="ia-x")
APPROVED = Verdict(VerdictStatus.APPROVED, [], ORDER)


def _proposal(action=Action.BUY, symbol="SOLUSDT"):
    return Proposal(symbol=symbol, action=action, conviction=0.1,
                    rationale="x", cycle_id="c")


def _order_decision(decision_id, ts, symbol="SOLUSDT"):
    # ajuste autorizado (ruling do controller, C2): as 3 chamadas deste
    # helper representam ordens que de fato EXECUTARAM (histórico real de
    # trade), então precisam de fills_json — sem isso, desde que hitl.py
    # passou a exigir ordem executada (não só intenção registrada), essas
    # decisões deixariam de "contar" e as regras de primeiro trade/
    # pós-breaker disparariam de novo indevidamente nestes testes.
    return DecisionRecord(decision_id=decision_id, ts=ts, inputs_hash="h",
                          snapshot_json="{}", proposal_json="{}",
                          verdict_json="{}",
                          order_json=f'{{"symbol": "{symbol}"}}',
                          fills_json=f'{{"executed_qty": 1, "ts":'
                                     f' "{ts.isoformat()}"}}')


def test_primeiro_trade_exige_aprovacao(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.NEEDS_APPROVAL
    assert any("primeiro trade" in r for r in verdict.reasons)
    assert verdict.order == ORDER  # a ordem sobrevive p/ aprovação
    store.close()


def test_simbolo_ja_operado_nao_dispara(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_order_decision("d0", NOW - timedelta(days=3)))
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.APPROVED
    store.close()


def test_posicao_existente_nao_dispara(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    portfolio = PortfolioState(
        equity=10_000.0, cash=5_000.0,
        positions={"SOLUSDT": Position("SOLUSDT", 1.0, 90.0)})
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.APPROVED
    store.close()


def test_pos_breaker_exige_aprovacao(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_order_decision("d0", NOW - timedelta(days=2)))
    record_halt_if_needed(store, HaltLevel.DAY, NOW - timedelta(days=1))
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    # símbolo já operado (d0) → regra 1 não dispara; regra 2 sim
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.NEEDS_APPROVAL
    assert any("circuit breaker" in r for r in verdict.reasons)
    store.close()


def test_ordem_depois_do_halt_ja_passou_nao_dispara(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.DAY, NOW - timedelta(days=2))
    store.append_decision(_order_decision("d1", NOW - timedelta(days=1)))
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.APPROVED
    store.close()


def test_pedido_rejeitado_nao_desarma_regra_de_primeiro_trade(tmp_path):
    # C2 (cenário 1 da revisão): o primeiro pedido de BUY em SOLUSDT virou
    # NEEDS_APPROVAL e foi registrado com order_json — mas o dono REJEITOU,
    # nunca executou (fills_json fica None). Um pedido seguinte no mesmo
    # símbolo tem que disparar a regra de novo — sem isto, bastava um
    # pedido rejeitado/expirado para nunca mais exigir aprovação humana.
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(DecisionRecord(
        decision_id="d0", ts=NOW - timedelta(hours=2), inputs_hash="h",
        snapshot_json="{}", proposal_json="{}",
        verdict_json='{"status": "needs_approval", "reasons": []}',
        order_json='{"symbol": "SOLUSDT"}'))  # sem fills_json
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.NEEDS_APPROVAL
    assert any("primeiro trade" in r for r in verdict.reasons)
    store.close()


def test_dry_run_aprovado_nao_desarma_regra_de_primeiro_trade(tmp_path):
    # C2 (cenário 2 da revisão): um ciclo --dry-run propôs BUY em SOLUSDT
    # e o veredito foi APPROVED (não precisava de HITL) — mas dry_run
    # impediu a execução real, então fills_json fica None. Isto não pode
    # contar como "já operei este símbolo".
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(DecisionRecord(
        decision_id="d0", ts=NOW - timedelta(hours=2), inputs_hash="h",
        snapshot_json="{}", proposal_json="{}",
        verdict_json='{"status": "approved", "reasons": []}',
        order_json='{"symbol": "SOLUSDT"}'))  # sem fills_json: dry-run
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.NEEDS_APPROVAL
    assert any("primeiro trade" in r for r in verdict.reasons)
    store.close()


def test_pedido_pos_breaker_rejeitado_nao_desarma_regra_2(tmp_path):
    # C2 (cenário 3 da revisão): a regra pós-breaker disparou (d1, sem
    # fills_json — o dono rejeitou). O PRÓXIMO pedido depois do breaker
    # tem que disparar a regra de novo — sem isto, um único pedido
    # rejeitado/expirado logo após o breaker bastava para nunca mais
    # exigir aprovação, furando a rede de segurança do /retomar.
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_order_decision("d0", NOW - timedelta(days=2)))
    record_halt_if_needed(store, HaltLevel.DAY, NOW - timedelta(days=1))
    store.append_decision(DecisionRecord(
        decision_id="d1", ts=NOW - timedelta(hours=1), inputs_hash="h",
        snapshot_json="{}", proposal_json="{}",
        verdict_json='{"status": "needs_approval", "reasons": []}',
        order_json='{"symbol": "SOLUSDT"}'))  # rejeitado: sem fills_json
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.NEEDS_APPROVAL
    assert any("circuit breaker" in r for r in verdict.reasons)
    store.close()


def test_rejected_e_hold_intocados(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    rejected = Verdict(VerdictStatus.REJECTED, ["motivo"], None)
    assert apply_hitl_overrides(rejected, _proposal(), portfolio,
                                store, NOW) is rejected
    hold_ok = Verdict(VerdictStatus.APPROVED, [], None)  # HOLD: sem ordem
    assert apply_hitl_overrides(hold_ok, _proposal(Action.HOLD), portfolio,
                                store, NOW) is hold_ok
    store.close()
