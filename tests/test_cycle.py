# tests/test_cycle.py
from datetime import datetime, timedelta, timezone

import pytest

from invest_agent.config import MODERADO
from invest_agent.data.models import Candle
from invest_agent.data.store import CandleStore
from invest_agent.engine import RulesEngine
from invest_agent.killswitch import KillSwitch
from invest_agent.models import Action, Proposal
from invest_agent.orchestrator.cycle import CycleResult, run_cycle
from invest_agent.settings import Settings
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 5, tzinfo=timezone.utc)


class FakeAdapter:
    """Simula saldos free/locked como a Binance real (C1): place_stop_loss
    trava a quantidade do ativo base (free -> locked) e cancel_order
    destrava de volta. É o que torna a trava do C1 (posição não pode
    sumir com o saldo preso num stop GTC) testável sem rede."""

    def __init__(self, price=100.0, bid=99.9, ask=100.0,
                 balances=None, fill_qty=None, stop_loss_error=None):
        self.price, self.bid, self.ask = price, bid, ask
        balances = balances or {"USDT": 10_000.0}
        self._balances = {asset: [free, 0.0]
                          for asset, free in balances.items()}
        self.fill_qty = fill_qty
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
        quote = qty * order.limit_price
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
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW,
                       dry_run=True)
    assert result.verdict_status == "approved" and result.executed is False
    assert adapter.orders == []
    (rec,) = store.read_decisions()
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
