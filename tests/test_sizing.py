from invest_agent.config import MODERADO
from invest_agent.sizing import compute_buy_qty


def test_sizing_basico_convictao_cheia():
    # equity 1000, teto 10% => alvo 100 USDT a preço 50 => 2.0 unidades
    qty = compute_buy_qty(equity=1000.0, price=50.0, conviction=1.0,
                          current_position_notional=0.0,
                          total_invested_notional=0.0, profile=MODERADO)
    assert qty == 2.0


def test_convictao_modula_o_tamanho():
    qty = compute_buy_qty(equity=1000.0, price=50.0, conviction=0.5,
                          current_position_notional=0.0,
                          total_invested_notional=0.0, profile=MODERADO)
    assert qty == 1.0  # 50 USDT / 50


def test_corta_pelo_teto_por_ativo():
    # já tem 80 USDT no ativo; teto 100 => só cabem 20
    qty = compute_buy_qty(equity=1000.0, price=10.0, conviction=1.0,
                          current_position_notional=80.0,
                          total_invested_notional=80.0, profile=MODERADO)
    assert qty == 2.0  # 20 USDT / 10


def test_corta_pelo_teto_de_exposicao_total():
    # exposição já em 590 de 600 => só cabem 10
    qty = compute_buy_qty(equity=1000.0, price=10.0, conviction=1.0,
                          current_position_notional=0.0,
                          total_invested_notional=590.0, profile=MODERADO)
    assert qty == 1.0  # 10 USDT / 10


def test_abaixo_do_minimo_da_binance_retorna_zero():
    # espaço restante de 5 USDT < min_notional 10 => 0
    qty = compute_buy_qty(equity=1000.0, price=10.0, conviction=1.0,
                          current_position_notional=95.0,
                          total_invested_notional=95.0, profile=MODERADO)
    assert qty == 0.0
