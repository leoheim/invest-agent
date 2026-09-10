import pytest

from invest_agent.backtest.costs import (
    CostModel, buy_and_hold_return, max_drawdown, trade_return,
)


def test_precos_efetivos_com_slippage_e_taxa():
    costs = CostModel(fee_pct=0.001, slippage_pct=0.0005)
    # compra: paga slippage para cima; venda: sofre slippage para baixo
    assert costs.buy_price(100.0) == pytest.approx(100.05)
    assert costs.sell_price(100.0) == pytest.approx(99.95)


def test_trade_return_ida_e_volta_com_custos():
    costs = CostModel(fee_pct=0.001, slippage_pct=0.0005)
    # entra a 100, sai a 110:
    # custo efetivo de entrada = 100*1.0005*1.001 = 100.15005
    # recebido na saída       = 110*0.9995*0.999  = 109.835055...
    esperado = (110 * 0.9995 * 0.999) / (100 * 1.0005 * 1.001) - 1
    assert trade_return(100.0, 110.0, costs) == pytest.approx(esperado)


def test_trade_return_sem_custos_e_o_retorno_bruto():
    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    assert trade_return(100.0, 110.0, zero) == pytest.approx(0.10)


def test_buy_and_hold_return():
    costs = CostModel(fee_pct=0.001, slippage_pct=0.0005)
    esperado = (120 * 0.9995 * 0.999) / (100 * 1.0005 * 1.001) - 1
    assert buy_and_hold_return(100.0, 120.0, costs) == pytest.approx(esperado)


def test_max_drawdown_calculado_a_mao():
    # picos: 100, 120; vale pós-pico-120: 90 → dd = (120-90)/120 = 0.25
    assert max_drawdown([100.0, 120.0, 90.0, 110.0]) == pytest.approx(0.25)


def test_max_drawdown_serie_crescente_e_zero():
    assert max_drawdown([1.0, 2.0, 3.0]) == 0.0


def test_max_drawdown_vazio_e_zero():
    assert max_drawdown([]) == 0.0
