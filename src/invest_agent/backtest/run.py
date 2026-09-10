"""CLI do backtest: sweep grosseiro em Python puro escolhe os parâmetros,
backtrader dá os fills realistas, relatório compara com buy-and-hold.
O main() é borda de composição (única leitura de argv/CLI; sem relógio —
o período vem dos dados ou dos argumentos)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from ..data.store import CandleStore
from .costs import CostModel
from .engine_bt import run_backtrader
from .report import build_report
from .strategies import STRATEGIES
from .sweep import run_sweep


def backtest_symbol(store: CandleStore, symbol: str, interval: str,
                    strategy_name: str, costs: CostModel,
                    start: datetime | None = None,
                    end: datetime | None = None,
                    initial_cash: float = 10_000.0,
                    split: float | None = None) -> str:
    if strategy_name not in STRATEGIES:
        raise ValueError(f"estratégia desconhecida: {strategy_name} "
                         f"(disponíveis: {', '.join(sorted(STRATEGIES))})")
    candles = store.read(symbol, interval, start=start, end=end)
    if not candles:
        raise ValueError(f"sem candles para {symbol} {interval} no store — "
                         "rode a ingestão primeiro")
    signal_fn, grid = STRATEGIES[strategy_name]

    if split is not None:
        if not 0.0 < split < 1.0:
            raise ValueError(f"split deve estar entre 0 e 1: {split}")
        corte = int(len(candles) * split)
        treino, teste = candles[:corte], candles[corte:]
        if len(treino) < 2 or len(teste) < 2:
            raise ValueError("split deixa treino ou teste sem candles")
        sweep = run_sweep(treino, signal_fn, grid, costs)
        best = sweep[0]
        closes = [c.close for c in teste]
        signals = signal_fn(closes, **best.params)
        run = run_backtrader(teste, signals, costs, initial_cash=initial_cash)
        return build_report(symbol, interval, best.params, run, teste, costs,
                            sweep=sweep, out_of_sample=True)
    else:
        sweep = run_sweep(candles, signal_fn, grid, costs)
        best = sweep[0]
        closes = [c.close for c in candles]
        signals = signal_fn(closes, **best.params)
        run = run_backtrader(candles, signals, costs,
                             initial_cash=initial_cash)
        return build_report(symbol, interval, best.params, run, candles, costs,
                            sweep=sweep)


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Backtest honesto vs buy-and-hold (custos incluídos)")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--strategy", default="sma_cross",
                        choices=sorted(STRATEGIES))
    parser.add_argument("--start", type=_parse_date, default=None)
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--root", default=Path("data/candles"), type=Path)
    parser.add_argument("--cash", type=float, default=10_000.0)
    parser.add_argument("--fee", type=float, default=0.001)
    parser.add_argument("--slippage", type=float, default=0.0005)
    parser.add_argument("--split", type=float, default=None)
    args = parser.parse_args(argv)

    costs = CostModel(fee_pct=args.fee, slippage_pct=args.slippage)
    store = CandleStore(args.root)
    print(backtest_symbol(store, args.symbol, args.interval, args.strategy,
                          costs, start=args.start, end=args.end,
                          initial_cash=args.cash, split=args.split))


if __name__ == "__main__":
    main()
