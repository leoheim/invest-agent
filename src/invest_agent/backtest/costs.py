"""Modelo de custos do backtest (spec §5, Fase 1: custos 0,10% + slippage
modelados) e métricas puras. Taxa e slippage são POR LADO: compra paga
preço*(1+slippage) e taxa sobre o valor; venda recebe preço*(1-slippage)
menos a taxa."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    fee_pct: float = 0.001       # 0,10% por lado (taker Binance spot)
    slippage_pct: float = 0.0005  # 0,05% por lado

    def buy_price(self, price: float) -> float:
        """Preço efetivo pago na compra (slippage contra o comprador)."""
        return price * (1 + self.slippage_pct)

    def sell_price(self, price: float) -> float:
        """Preço efetivo recebido na venda (slippage contra o vendedor)."""
        return price * (1 - self.slippage_pct)


def trade_return(entry_price: float, exit_price: float,
                 costs: CostModel) -> float:
    """Retorno líquido de um trade ida-e-volta, com slippage e taxa nos
    dois lados."""
    paid = costs.buy_price(entry_price) * (1 + costs.fee_pct)
    received = costs.sell_price(exit_price) * (1 - costs.fee_pct)
    return received / paid - 1


def buy_and_hold_return(first_open: float, last_close: float,
                        costs: CostModel) -> float:
    """Baseline honesto: compra na primeira abertura, vende no último
    fechamento, mesmos custos do backtest."""
    return trade_return(first_open, last_close, costs)


def max_drawdown(equity: list[float]) -> float:
    """Máxima queda pico-a-vale como fração positiva (0.25 = -25%)."""
    peak = float("-inf")
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst
