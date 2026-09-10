"""Relatório do backtest em português: estratégia vs buy-and-hold com os
MESMOS custos (o critério de saída da Fase 1, spec §5)."""
from __future__ import annotations

from ..data.models import Candle
from .costs import CostModel, buy_and_hold_return, max_drawdown
from .engine_bt import BacktestRun
from .sweep import SweepResult


def build_report(symbol: str, interval: str, params: dict,
                 run: BacktestRun, candles: list[Candle], costs: CostModel,
                 sweep: list[SweepResult] | None = None,
                 out_of_sample: bool = False) -> str:
    strategy_return = run.final_value / run.initial_cash - 1
    baseline = buy_and_hold_return(candles[0].open, candles[-1].close, costs)
    drawdown = max_drawdown(run.equity_curve)
    periodo = (f"{candles[0].open_time:%Y-%m-%d} a "
               f"{candles[-1].close_time:%Y-%m-%d}")
    veredito = ("SUPERA o buy-and-hold após custos"
                if strategy_return > baseline
                else "NÃO SUPERA o buy-and-hold após custos")

    aviso = ("Validação out-of-sample: parâmetros escolhidos no treino; veredito no período de teste."
             if out_of_sample
             else "Aviso: parâmetros escolhidos in-sample (mesma janela do veredito) — resultado otimista; trate como triagem, não validação out-of-sample.")

    linhas = [
        f"# Backtest {symbol} {interval} — {periodo}",
        "",
        f"Parâmetros: {params}",
        f"Custos: taxa {costs.fee_pct:.2%} + slippage {costs.slippage_pct:.2%} por lado",
        "",
        f"Retorno da estratégia: {strategy_return:+.2%}",
        f"Retorno buy-and-hold:  {baseline:+.2%}",
        f"Max drawdown:          {drawdown:.2%}",
        f"Trades fechados:       {run.n_trades}",
        "",
        f"Veredito: {veredito}",
        aviso,
    ]
    if sweep:
        linhas += ["", "## Top do sweep (retorno bruto simulado)"]
        for r in sweep[:5]:
            linhas.append(f"- {r.params}: {r.total_return:+.2%} "
                          f"({r.n_trades} trades)")
    return "\n".join(linhas)
