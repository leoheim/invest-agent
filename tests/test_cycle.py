# tests/test_cycle.py
from datetime import datetime, timedelta, timezone

import pytest

from invest_agent.config import MODERADO
from invest_agent.data.models import Candle
from invest_agent.data.store import CandleStore
from invest_agent.engine import RulesEngine
from invest_agent.killswitch import KillSwitch
from invest_agent.models import Action, Proposal
from invest_agent.orchestrator.cycle import CycleResult, llm_pre_gates, run_cycle
from invest_agent.settings import Settings
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 5, tzinfo=timezone.utc)


class FakeAdapter:
    """Simula saldos free/locked como a Binance real (C1): place_stop_loss
    trava a quantidade do ativo base (free -> locked) e cancel_order
    destrava de volta. É o que torna a trava do C1 (posição não pode
    sumir com o saldo preso num stop GTC) testável sem rede."""

    def __init__(self, price=100.0, bid=99.9, ask=100.0,
                 balances=None, fill_qty=None, fill_price=None,
                 stop_loss_error=None):
        self.price, self.bid, self.ask = price, bid, ask
        balances = balances or {"USDT": 10_000.0}
        self._balances = {asset: [free, 0.0]
                          for asset, free in balances.items()}
        self.fill_qty = fill_qty
        # I3: permite simular um IOC que preenche a um preço diferente do
        # limite da ordem (drift entre a proposta e a execução real).
        self.fill_price = fill_price
        self.stop_loss_error = stop_loss_error
        self.orders, self.stops, self.cancels = [], [], []
        self._locks = {}  # client_order_id -> (asset, qty travada)

    def get_balances(self):
        return {asset: (free, locked)
                for asset, (free, locked) in self._balances.items()
                if free + locked > 0}

    def get_price(self, symbol):
        return self.price

    def get_book(self, symbol):
        return self.bid, self.ask

    def place_limit_ioc(self, order):
        self.orders.append(order)
        qty = self.fill_qty if self.fill_qty is not None else order.qty
        price = (self.fill_price if self.fill_price is not None
                 else order.limit_price)
        quote = qty * price
        asset = order.symbol.removesuffix("USDT")
        base = self._balances.setdefault(asset, [0.0, 0.0])
        cash = self._balances.setdefault("USDT", [0.0, 0.0])
        if order.side == "BUY":
            base[0] += qty
            cash[0] -= quote
        else:
            base[0] -= qty
            cash[0] += quote
        return {"status": "FILLED" if qty == order.qty else "EXPIRED",
                "executedQty": str(qty),
                "cummulativeQuoteQty": str(quote)}

    def place_stop_loss(self, symbol, qty, stop_price, client_order_id):
        if self.stop_loss_error is not None:
            raise self.stop_loss_error
        self.stops.append((symbol, qty, stop_price, client_order_id))
        asset = symbol.removesuffix("USDT")
        bal = self._balances.setdefault(asset, [0.0, 0.0])
        bal[0] -= qty
        bal[1] += qty
        self._locks[client_order_id] = (asset, qty)
        return {"status": "NEW"}

    def cancel_order(self, symbol, client_order_id):
        self.cancels.append((symbol, client_order_id))
        lock = self._locks.pop(client_order_id, None)
        if lock:
            asset, qty = lock
            bal = self._balances.setdefault(asset, [0.0, 0.0])
            bal[0] += qty
            bal[1] -= qty
        return {}


def _historical_order_decision(store, symbol="BTCUSDT", ts=None):
    """Ajuste autorizado (ruling do controller): insere uma ordem antiga
    para `symbol` no decision_log, para que a regra HITL de "primeiro
    trade" (Task 4) não dispare em testes que já existiam antes dela.
    Ajuste C2: passou a incluir fills_json — desde que hitl.py exige ordem
    EXECUTADA (não só intenção registrada) para desarmar a regra, este
    histórico precisa representar um trade que de fato aconteceu."""
    from invest_agent.storage.sqlite_store import DecisionRecord
    ts = ts or (NOW - timedelta(days=3))
    store.append_decision(DecisionRecord(
        decision_id=f"hist-{symbol}", ts=ts,
        inputs_hash="h", snapshot_json="{}", proposal_json="{}",
        verdict_json="{}", order_json=f'{{"symbol": "{symbol}"}}',
        fills_json=f'{{"executed_qty": 1, "ts": "{ts.isoformat()}"}}'))


def _fixture(tmp_path, proposal=None, adapter=None):
    store = SqliteStore(tmp_path / "a.db")
    candle_store = CandleStore(tmp_path / "candles")
    candles = []
    for h in range(30, 0, -1):
        open_time = NOW.replace(minute=0) - timedelta(hours=h)
        candles.append(Candle(symbol="BTCUSDT", interval="1h",
                              open_time=open_time, open=100.0, high=100.0,
                              low=100.0, close=100.0, volume=1.0,
                              quote_volume=1_000_000.0, n_trades=10,
                              close_time=open_time + timedelta(minutes=59,
                                                               seconds=59)))
    candle_store.append(candles)
    settings = Settings(kill_switch_path=tmp_path / "KILL",
                        heartbeat_path=tmp_path / "hb")
    engine = RulesEngine(MODERADO, frozenset({"BTCUSDT", "ETHUSDT"}),
                         KillSwitch(settings.kill_switch_path))
    adapter = adapter or FakeAdapter()

    def proposer(context):
        return proposal or Proposal(symbol="BTCUSDT", action=Action.HOLD,
                                    conviction=0.0, rationale="x",
                                    cycle_id=context["cycle_id"])

    return store, candle_store, adapter, engine, proposer, settings


def test_hold_registra_decisao_sem_ordem(tmp_path):
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert isinstance(result, CycleResult)
    assert result.verdict_status == "approved" and result.executed is False
    (rec,) = store.read_decisions()
    assert rec.order_json is None and rec.inputs_hash
    assert settings.heartbeat_path.exists()
    store.close()


def test_buy_pequeno_executa_e_poe_stop(tmp_path):
    # equity 10k; conviction 0.019 → alvo 19 USDT < 2% (200) → APPROVED
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    # ajuste autorizado (Task 4 HITL duro): sem isto, o primeiro trade em
    # BTCUSDT dispara "primeiro trade" e o veredito vira needs_approval.
    _historical_order_decision(store)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.executed is True
    assert len(adapter.orders) == 1 and adapter.orders[0].side == "BUY"
    assert len(adapter.stops) == 1
    positions = store.get_positions()
    assert "BTCUSDT" in positions
    qty, avg, stop_id = positions["BTCUSDT"]
    assert qty == adapter.orders[0].qty and avg == 100.0
    assert stop_id.endswith("-sl")
    store.close()


def test_buy_grande_vira_pendencia_hitl(tmp_path):
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=1.0,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.verdict_status == "needs_approval"
    assert adapter.orders == []
    (pend,) = store.get_pending()
    assert pend[2] == NOW + timedelta(minutes=10)  # TTL 10 min
    store.close()


def test_pendencia_vencida_expira_no_ciclo_seguinte(tmp_path):
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    store.add_pending("d-velha", NOW - timedelta(hours=1),
                      NOW - timedelta(minutes=50))
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert store.get_pending() == []  # expirada = cancelada
    store.close()


def test_halt_ativo_bloqueia_o_ciclo(tmp_path):
    from invest_agent.breakers import HaltLevel
    from invest_agent.orchestrator.state import record_halt_if_needed
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    record_halt_if_needed(store, HaltLevel.MONTH, NOW)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.verdict_status == "halted" and result.halted == "MONTH"
    assert store.read_decisions() == []  # nem propôs
    store.close()


def test_custo_de_api_acima_do_teto_halta(tmp_path):
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    store.add_api_cost(NOW.date(), settings.api_cost_daily_cap_usd + 0.01)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.verdict_status == "halted"
    assert "custo" in " ".join(result.reasons)
    store.close()


def test_dry_run_nao_envia_ordem(tmp_path):
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    # ajuste autorizado (Task 4 HITL duro): idem test_buy_pequeno... — sem
    # histórico, o primeiro trade em BTCUSDT dispara needs_approval, o que
    # preempta o próprio dry_run (a checagem de NEEDS_APPROVAL vem antes).
    _historical_order_decision(store)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW,
                       dry_run=True)
    assert result.verdict_status == "approved" and result.executed is False
    assert adapter.orders == []
    rec = store.read_decisions()[-1]  # a última é a deste ciclo (a 1a é o
    # histórico inserido pelo ajuste HITL acima — read_decisions ordena por
    # ts, e o histórico tem ts anterior a NOW)
    assert rec.order_json is not None  # a decisão fica registrada
    store.close()


def test_sell_cancela_stop_antigo_e_reduz_posicao(tmp_path):
    prop = Proposal(symbol="BTCUSDT", action=Action.CLOSE, conviction=0.5,
                    rationale="x", cycle_id="2026091012")
    adapter = FakeAdapter(balances={"USDT": 10_000.0, "BTC": 0.001})
    store, cs, _, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop, adapter=adapter)
    store.upsert_position("BTCUSDT", 0.001, 90.0, "ia-old-sl")
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.executed is True
    assert adapter.cancels == [("BTCUSDT", "ia-old-sl")]
    assert adapter.orders[0].side == "SELL"
    assert store.get_positions() == {}  # posição zerada
    store.close()


def test_sell_parcial_recoloca_stop_no_remanescente(tmp_path):
    prop = Proposal(symbol="BTCUSDT", action=Action.CLOSE, conviction=0.5,
                    rationale="x", cycle_id="2026091012")
    adapter = FakeAdapter(balances={"USDT": 10_000.0, "BTC": 1.0},
                         fill_qty=0.6)
    store, cs, _, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop, adapter=adapter)
    store.upsert_position("BTCUSDT", 1.0, 90.0, "ia-old-sl")
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.executed is True
    order = adapter.orders[0]
    assert order.side == "SELL"
    positions = store.get_positions()
    assert "BTCUSDT" in positions  # remanescente continua com posição
    qty, avg, stop_id = positions["BTCUSDT"]
    assert qty == pytest.approx(0.4)
    assert stop_id is not None and stop_id.endswith("-sl")
    assert (order.symbol, pytest.approx(0.4),
            pytest.approx(order.limit_price * 0.95), stop_id) in adapter.stops
    store.close()


def test_breaker_persiste_mesmo_em_ciclo_hold(tmp_path):
    adapter = FakeAdapter(balances={"USDT": 8_000.0})  # queda de 20% → MONTH
    store, cs, _, engine, proposer, settings = _fixture(
        tmp_path, adapter=adapter)
    for period in ("day", "week", "month"):
        store.set_mark(period, 10_000.0, NOW)  # mesmo dia — não rola
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.verdict_status == "approved"  # HOLD segue seu curso normal
    halt = store.get_halt()
    assert halt is not None and halt[0] == "MONTH"
    result2 = run_cycle(store, cs, adapter, engine, proposer, settings,
                        NOW + timedelta(hours=1))
    assert result2.verdict_status == "halted"
    store.close()


def test_ciclo_apos_buy_com_stop_travado_nao_apaga_posicao_nem_halta(tmp_path):
    # C1: depois de um BUY executado, o stop GTC trava o ativo base
    # (free -> locked) na Binance real. O ciclo seguinte não pode achar a
    # posição zerada (free=0) nem disparar um breaker espúrio pela "perda"
    # de equity.
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    # ajuste autorizado (Task 4 HITL duro): idem test_buy_pequeno... — sem
    # histórico, o primeiro trade em BTCUSDT dispara needs_approval.
    _historical_order_decision(store)
    result1 = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result1.executed is True
    qty_bought = adapter.orders[0].qty
    free, locked = adapter.get_balances()["BTC"]
    assert free == pytest.approx(0.0) and locked == pytest.approx(qty_bought)

    def hold_proposer(context):
        return Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                        rationale="x", cycle_id=context["cycle_id"])

    result2 = run_cycle(store, cs, adapter, engine, hold_proposer, settings,
                        NOW + timedelta(hours=1))
    assert result2.verdict_status != "halted"
    assert store.get_halt() is None  # nenhum breaker espúrio disparado

    positions = store.get_positions()
    assert "BTCUSDT" in positions  # a posição não sumiu
    qty, avg, stop_id = positions["BTCUSDT"]
    assert qty == pytest.approx(qty_bought)
    assert stop_id is not None
    store.close()


def test_buy_com_falha_no_stop_ainda_deixa_posicao_rastreada(tmp_path):
    # I1: se place_stop_loss falhar depois do fill, a posição não pode
    # ficar invisível (moedas na carteira sem stop e sem linha em
    # `positions`). A falha ainda propaga (fail-closed), mas a posição
    # precisa existir com stop_order_id=None.
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    adapter = FakeAdapter(stop_loss_error=RuntimeError("falha ao colocar stop"))
    store, cs, _, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop, adapter=adapter)
    # ajuste autorizado (Task 4 HITL duro): idem test_buy_pequeno... — sem
    # histórico, o primeiro trade em BTCUSDT dispara needs_approval e o
    # ciclo nunca chegaria em _execute (logo nunca levantaria o RuntimeError
    # que este teste existe para verificar).
    _historical_order_decision(store)
    with pytest.raises(RuntimeError):
        run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    positions = store.get_positions()
    assert "BTCUSDT" in positions
    qty, avg, stop_id = positions["BTCUSDT"]
    assert qty == adapter.orders[0].qty
    assert stop_id is None  # o stop falhou, mas a posição ficou rastreada
    store.close()


def test_buy_em_posicao_existente_cancela_stop_antigo_e_poe_um_novo(tmp_path):
    # I2: um BUY sobre posição existente não pode deixar o stop antigo
    # orfão (resting na exchange, id perdido, qty travada). Cancela o
    # antigo e coloca UM único stop novo cobrindo o total.
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    adapter = FakeAdapter(balances={"USDT": 10_000.0, "BTC": 0.05})
    store, cs, _, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop, adapter=adapter)
    # posição existente com stop já resting (simula ciclo anterior) — o
    # saldo correspondente já está travado (free -> locked), como na
    # Binance real
    store.upsert_position("BTCUSDT", 0.05, 90.0, "ia-old-sl")
    adapter.place_stop_loss("BTCUSDT", 0.05, 85.5, "ia-old-sl")
    adapter.stops.clear()  # só nos interessa o(s) stop(s) deste ciclo

    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)

    assert result.executed is True
    assert ("BTCUSDT", "ia-old-sl") in adapter.cancels  # stop antigo cancelado
    assert len(adapter.stops) == 1  # um único stop novo — não dois
    symbol, qty, stop_price, new_stop_id = adapter.stops[0]
    executed_qty = adapter.orders[0].qty
    assert qty == pytest.approx(0.05 + executed_qty)  # cobre a posição toda
    assert new_stop_id != "ia-old-sl"

    total_qty, avg, saved_stop_id = store.get_positions()["BTCUSDT"]
    assert total_qty == pytest.approx(0.05 + executed_qty)
    assert saved_stop_id == new_stop_id

    # cancelar o stop antigo liberou a trava — sem saldo órfão
    free, locked = adapter.get_balances()["BTC"]
    assert locked == pytest.approx(total_qty)
    assert free == pytest.approx(0.0)
    store.close()


def test_ciclo_com_llm_proposer_integrado(tmp_path):
    """Integração: proposer LLM fake propõe BUY pequeno → ciclo executa."""
    import json as _json
    from types import SimpleNamespace
    from invest_agent.brain.client import LlmClient
    from invest_agent.brain.proposer import make_llm_proposer

    respostas = [
        {"candidates": [{"symbol": "BTCUSDT", "reason": "r"}]},
        {"symbol": "BTCUSDT", "action": "buy", "conviction": 0.019,
         "rationale": "sinal", "urgency": "baixa"},
    ]
    state = {"i": 0}

    def create_fn(**kwargs):
        payload = respostas[state["i"]]
        state["i"] += 1
        usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        block = SimpleNamespace(type="text", text=_json.dumps(payload))
        return SimpleNamespace(content=[block], stop_reason="end_turn",
                               usage=usage)

    store, cs, adapter, engine, _, settings = _fixture(tmp_path)
    # ajuste autorizado (Task 4 HITL duro): idem test_buy_pequeno... — sem
    # histórico, o primeiro trade em BTCUSDT dispara needs_approval.
    _historical_order_decision(store)
    proposer = make_llm_proposer(LlmClient(create_fn=create_fn), store,
                                 lambda: NOW)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.executed is True
    assert store.api_cost_today(NOW.date()) > 0
    store.close()


def test_ciclo_passa_news_e_macro_ao_contexto(tmp_path):
    """O contexto registrado no decision_log carrega news e macro."""
    import json as _json
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    news = [("Bitcoin sobe", "coindesk", ("BTCUSDT",),
             "2026-09-10T10:00:00+00:00", "positivo", 4)]
    macro = {"fng": 34.0}
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW,
              news=news, macro=macro)
    (rec,) = store.read_decisions()
    snapshot = _json.loads(rec.snapshot_json)
    assert snapshot["news"][0]["title"] == "Bitcoin sobe"
    assert snapshot["macro"]["fng"] == 34.0
    store.close()


def test_llm_pre_gates_normal_libera_gasto(tmp_path):
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    assert llm_pre_gates(store, settings, NOW) is True
    store.close()


def test_llm_pre_gates_com_halt_ativo_bloqueia(tmp_path):
    from invest_agent.breakers import HaltLevel
    from invest_agent.orchestrator.state import record_halt_if_needed
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    record_halt_if_needed(store, HaltLevel.MONTH, NOW)
    assert llm_pre_gates(store, settings, NOW) is False
    store.close()


def test_llm_pre_gates_com_custo_no_teto_bloqueia(tmp_path):
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)
    store.add_api_cost(NOW.date(), settings.api_cost_daily_cap_usd)
    assert llm_pre_gates(store, settings, NOW) is False
    store.close()


def test_hitl_primeiro_trade_downgrade_no_ciclo(tmp_path):
    # BUY pequeno (0.019 → 19 USDT < 2%) seria APPROVED; mas é o primeiro
    # trade do símbolo → NEEDS_APPROVAL via override
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.verdict_status == "needs_approval"
    assert any("primeiro trade" in r for r in result.reasons)
    assert adapter.orders == []
    assert len(store.get_pending()) == 1
    store.close()


def test_aprovado_executa_no_ciclo_seguinte(tmp_path):
    import json as _json
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    (pend,) = store.get_pending()
    store.set_pending_status(pend[0], "approved")  # dono aprovou no bot
    avisos = []
    hold = Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale="x", cycle_id="2026091013")
    result = run_cycle(store, cs, adapter, engine, lambda ctx: hold,
                       settings, NOW + timedelta(hours=1),
                       notifier=avisos.append)
    assert len(adapter.orders) == 1  # a ordem aprovada foi enviada
    assert adapter.orders[0].client_order_id.endswith(
        _json.loads(store.get_decision(pend[0]).order_json)[
            "client_order_id"][-5:])
    assert store.get_pending() == []  # virou executed
    assert any("aprovada" in a for a in avisos)
    store.close()


def test_notifier_falha_nao_impede_execucao_da_aprovada(tmp_path):
    # I2: substitui o teste antigo, que usava um proposer HOLD e nunca
    # chamava o notifier (approved_pending() vazio na store nova, então o
    # loop de _execute_approved nem executava). Este exercita de verdade o
    # contrato "notificação nunca derruba o ciclo": notifier explode, mas a
    # ordem aprovada ainda é enviada e o ciclo completa normalmente.
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    (pend,) = store.get_pending()
    decision_id = pend[0]
    store.set_pending_status(decision_id, "approved")

    def notifier_ruim(texto):
        raise RuntimeError("telegram fora do ar")

    hold = Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale="x", cycle_id="2026091013")
    result = run_cycle(store, cs, adapter, engine, lambda ctx: hold, settings,
                       NOW + timedelta(hours=1), notifier=notifier_ruim)
    assert result.verdict_status == "approved"  # ciclo completou
    assert len(adapter.orders) == 1  # a ordem aprovada foi enviada mesmo assim
    status = store._con.execute(
        "SELECT status FROM pending_approvals WHERE decision_id=?",
        (decision_id,)).fetchone()[0]
    assert status == "executed"
    store.close()


def test_hitl_notifier_falha_nao_impede_pendencia(tmp_path):
    # I2: segundo teste de substituição — cenário NEEDS_APPROVAL (primeiro
    # trade do símbolo) com hitl_notifier que explode. O ciclo precisa
    # completar e a pendência precisa ser criada normalmente.
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)

    def hitl_notifier_ruim(decision_id, order, reasons):
        raise RuntimeError("telegram fora do ar")

    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW,
                       hitl_notifier=hitl_notifier_ruim)
    assert result.verdict_status == "needs_approval"  # ciclo completou
    assert len(store.get_pending()) == 1  # pendência foi criada mesmo assim
    store.close()


def test_aprovada_que_falha_no_execute_marca_failed_e_nao_reenvia(tmp_path):
    # CRITICAL: sem try/except em _execute_approved, uma falha em _execute
    # (ex.: place_stop_loss) deixava a linha 'approved' para sempre — o
    # próximo ciclo reenviaria a MESMA ordem (mesmo client_order_id) de
    # novo, indefinidamente. O fix: marca 'failed' e re-levanta (fail-closed
    # preservado, mas sem retry automático).
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    adapter = FakeAdapter(stop_loss_error=RuntimeError("falha ao colocar stop"))
    store, cs, _, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop, adapter=adapter)
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    (pend,) = store.get_pending()
    decision_id = pend[0]
    store.set_pending_status(decision_id, "approved")

    avisos = []
    hold = Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale="x", cycle_id="2026091013")
    with pytest.raises(RuntimeError):
        run_cycle(store, cs, adapter, engine, lambda ctx: hold, settings,
                  NOW + timedelta(hours=1), notifier=avisos.append)

    assert len(adapter.orders) == 1  # a ordem foi enviada uma única vez
    assert store.approved_pending() == []  # não fica mais 'approved'
    status = store._con.execute(
        "SELECT status FROM pending_approvals WHERE decision_id=?",
        (decision_id,)).fetchone()[0]
    assert status == "failed"
    assert any("falhou" in a and "não será re-tentada" in a for a in avisos)

    # ciclo seguinte não reenvia a ordem (a linha não está mais 'approved')
    run_cycle(store, cs, adapter, engine, lambda ctx: hold, settings,
             NOW + timedelta(hours=2))
    assert len(adapter.orders) == 1  # nenhuma ordem nova
    store.close()


def test_halt_ativo_nao_executa_aprovada_espera_liberacao(tmp_path):
    # I3: ordens aprovadas não podem ser enviadas enquanto um halt está
    # ativo — a linha simplesmente espera a liberação, sem virar 'executed'
    # nem 'failed'.
    from invest_agent.breakers import HaltLevel
    from invest_agent.orchestrator.state import record_halt_if_needed
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    (pend,) = store.get_pending()
    decision_id = pend[0]
    store.set_pending_status(decision_id, "approved")

    record_halt_if_needed(store, HaltLevel.MONTH, NOW + timedelta(minutes=5))
    hold = Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale="x", cycle_id="2026091013")
    result = run_cycle(store, cs, adapter, engine, lambda ctx: hold, settings,
                       NOW + timedelta(hours=1))
    assert result.verdict_status == "halted"
    assert adapter.orders == []  # nada enviado — halt bloqueia antes
    assert store.approved_pending() == [decision_id]  # continua approved

    store.release_halt()
    result2 = run_cycle(store, cs, adapter, engine, lambda ctx: hold, settings,
                        NOW + timedelta(hours=2))
    assert result2.verdict_status != "halted"
    assert len(adapter.orders) == 1  # liberado — agora executa
    assert store.get_pending() == []
    store.close()


def test_aprovada_com_mais_de_24h_expira_sem_executar(tmp_path):
    # I3: uma aprovação nunca reavaliada por 24h+ não pode ser executada às
    # cegas — expira e pede re-aprovação.
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    (pend,) = store.get_pending()
    decision_id = pend[0]
    store.set_pending_status(decision_id, "approved")

    avisos = []
    hold = Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale="x", cycle_id="2026091013")
    run_cycle(store, cs, adapter, engine, lambda ctx: hold, settings,
             NOW + timedelta(hours=25), notifier=avisos.append)
    assert adapter.orders == []  # nada enviado
    status = store._con.execute(
        "SELECT status FROM pending_approvals WHERE decision_id=?",
        (decision_id,)).fetchone()[0]
    assert status == "expired"
    assert any("expirada" in a for a in avisos)
    store.close()


def test_kill_switch_ativo_nao_executa_aprovada(tmp_path):
    # C1: /kill, /pausar e o dead-man switch prometem parar TODA ordem
    # nova — mas o kill switch só era consultado dentro de engine.evaluate
    # (propostas novas), não antes de _execute_approved. Uma ordem já
    # aprovada antes do kill ligar seguia sendo enviada no próximo ciclo.
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    (pend,) = store.get_pending()
    decision_id = pend[0]
    store.set_pending_status(decision_id, "approved")

    engine.kill_switch.activate("teste C1")
    hold = Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale="x", cycle_id="2026091013")
    run_cycle(store, cs, adapter, engine, lambda ctx: hold, settings,
             NOW + timedelta(hours=1))
    assert adapter.orders == []  # nada enviado — kill switch ativo
    assert store.approved_pending() == [decision_id]  # continua approved

    engine.kill_switch.deactivate()
    run_cycle(store, cs, adapter, engine, lambda ctx: hold, settings,
             NOW + timedelta(hours=2))
    assert len(adapter.orders) == 1  # liberado — agora executa
    assert store.get_pending() == []
    store.close()


def test_aprovada_ja_em_execucao_por_outro_processo_e_ignorada(tmp_path):
    # I2: duas execuções concorrentes de _execute_approved (ex.: ciclo
    # manual do runbook §7 sobreposto ao cron) não podem ambas enviar a
    # mesma ordem aprovada. Simula uma reivindicação concorrente já em
    # andamento (status 'executing') e verifica que este ciclo não a toca.
    # A garantia atômica em si (só uma reivindicação vence) é testada
    # diretamente em test_sqlite_whitelist.py::test_claim_pending_e_atomico.
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    (pend,) = store.get_pending()
    decision_id = pend[0]
    store.set_pending_status(decision_id, "approved")
    store._con.execute(
        "UPDATE pending_approvals SET status='executing' WHERE decision_id=?",
        (decision_id,))
    store._con.commit()

    hold = Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale="x", cycle_id="2026091013")
    run_cycle(store, cs, adapter, engine, lambda ctx: hold, settings,
             NOW + timedelta(hours=1))
    assert adapter.orders == []  # nada enviado — já estava sendo processada
    status = store._con.execute(
        "SELECT status FROM pending_approvals WHERE decision_id=?",
        (decision_id,)).fetchone()[0]
    assert status == "executing"  # não foi tocada por este ciclo
    store.close()


def test_buy_executado_grava_fills_json_na_decisao(tmp_path):
    # C2: sem gravar fills_json, o decision_log não tinha como distinguir
    # ordem intencionada (registrada para HITL) de ordem de fato EXECUTADA
    # — o HITL obrigatório (primeiro trade/pós-breaker) não teria um sinal
    # confiável para desarmar.
    import json as _json
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    _historical_order_decision(store)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.executed is True
    decision_id = f"{result.cycle_id}-BTCUSDT"
    rec = store.get_decision(decision_id)
    assert rec.fills_json is not None
    fills = _json.loads(rec.fills_json)
    assert fills["executed_qty"] == pytest.approx(adapter.orders[0].qty)
    store.close()


def test_buy_com_drift_de_preco_usa_fill_para_o_stop(tmp_path):
    # I3: uma ordem aprovada pode ser executada até 24h depois, a preço de
    # mercado diferente do limite decidido na proposta. O stop tem que
    # partir do preço de FILL real, não do limite obsoleto — senão pode
    # nascer acima do mercado (após uma queda) e ser rejeitado na exchange.
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    adapter = FakeAdapter(fill_price=90.0)  # proposta usa ask=100; fill a 90
    store, cs, _, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop, adapter=adapter)
    _historical_order_decision(store)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.executed is True
    assert adapter.orders[0].limit_price == pytest.approx(100.0)
    symbol, qty, stop_price, stop_id = adapter.stops[0]
    assert stop_price == pytest.approx(90.0 * (1 - MODERADO.stop_loss_pct))
    assert stop_price != pytest.approx(
        adapter.orders[0].limit_price * (1 - MODERADO.stop_loss_pct))
    store.close()
