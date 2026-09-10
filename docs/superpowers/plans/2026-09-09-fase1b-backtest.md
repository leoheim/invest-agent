# Fase 1b — Backtest Honesto vs Buy-and-Hold — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest determinístico de estratégias mecânicas sobre os candles da Fase 1a, com custos de 0,10% + slippage modelados, fills realistas via backtrader, e comparação honesta contra baseline buy-and-hold — o critério de saída da Fase 1 (spec §5).

**Architecture:** Módulos novos sob `src/invest_agent/backtest/`: modelo de custos e métricas puras; geradores de sinal mecânicos (SMA cross, RSI reversão) construídos sobre `invest_agent.indicators`; varredura de parâmetros em Python puro (filtro grosseiro); execução com fills realistas no backtrader (ordem a mercado executa na ABERTURA do candle seguinte — sem look-ahead); relatório em português; CLI como única borda com relógio/dados reais. Sem LLM em backtest (spec §5: "backtest com LLM em janela antiga é suspeito").

**Tech Stack:** Python ≥ 3.12; runtime: duckdb, pyarrow (Fase 1a) + `backtrader>=1.9.78` (novo); dev: pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-invest-agent-design.md` (§5 Fase 1: "backtest (vectorbt varredura → backtrader fills realistas), custos 0,10% + slippage modelados; backtest honesto vs baseline buy-and-hold"). **Ruling do controller (registrado no ledger):** vectorbt não compila nesta máquina (llvmlite/numba falha o build em Python 3.12/macOS antigo); a etapa de varredura é substituída por um sweep determinístico em Python puro — papel idêntico (filtro grosseiro de parâmetros); o estágio de fills realistas permanece no backtrader conforme a spec.

## Global Constraints

- Python ≥ 3.12; runtime deps são SOMENTE `duckdb>=1.1`, `pyarrow>=17` e `backtrader>=1.9.78`; `pytest` só em dev.
- Custos: taxa **0,10% por lado** (`fee_pct=0.001`, taker da Binance) e **slippage 0,05% por lado** (`slippage_pct=0.0005`) como defaults; sempre parametrizáveis.
- **Sem look-ahead:** sinal calculado no fechamento do candle i só pode executar na ABERTURA do candle i+1. Nenhum indicador pode usar dados futuros.
- Nenhum teste acessa rede ou disco fora de `tmp_path`; nenhum módulo lê o relógio na lógica (exceção única: `main()` do CLI).
- Sem LLM em qualquer parte do backtest.
- Todas as datas timezone-aware UTC; mensagens e relatório em português; commits pequenos, sem assinatura.
- Módulos de `backtest/` podem importar `indicators` e `data/` (store/models); NUNCA importam o motor de regras, e o motor NUNCA importa `backtest/`.

---

### Task 1: Custos e métricas puras

**Files:**
- Create: `src/invest_agent/backtest/__init__.py`
- Create: `src/invest_agent/backtest/costs.py`
- Test: `tests/test_backtest_costs.py`

**Interfaces:**
- Consumes: nada.
- Produces: `CostModel` (dataclass frozen: `fee_pct: float = 0.001`, `slippage_pct: float = 0.0005`) com métodos `buy_price(price) -> float` (preço efetivo de compra, com slippage contra o comprador) e `sell_price(price) -> float`; `trade_return(entry_price, exit_price, costs) -> float`; `buy_and_hold_return(first_open, last_close, costs) -> float`; `max_drawdown(equity: list[float]) -> float` (fração positiva, 0.0 se nunca caiu). Tasks 3, 5 e 6 consomem estes nomes exatos.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_costs.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_backtest_costs.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'invest_agent.backtest'`

- [ ] **Step 3: Write minimal implementation**

`src/invest_agent/backtest/__init__.py`: arquivo vazio.

```python
# src/invest_agent/backtest/costs.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_backtest_costs.py -v`
Expected: PASS (7 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/backtest/ tests/test_backtest_costs.py
git commit -m "feat: modelo de custos (taxa+slippage) e métricas do backtest"
```

---

### Task 2: Geradores de sinal mecânicos

**Files:**
- Create: `src/invest_agent/backtest/strategies.py`
- Test: `tests/test_strategies.py`

**Interfaces:**
- Consumes: `sma`, `rsi` de `invest_agent.indicators` (Fase 1a).
- Produces: `Signal` (IntEnum: `ENTER = 1`, `EXIT = -1`, `HOLD = 0`); `sma_cross_signals(closes: list[float], fast: int, slow: int) -> list[int]`; `rsi_reversion_signals(closes: list[float], period: int = 14, low: float = 30.0, high: float = 70.0) -> list[int]`; `STRATEGIES: dict[str, ...]` mapeando nome → função com grade default de parâmetros (`{"sma_cross": (sma_cross_signals, GRID_SMA), "rsi_reversion": (rsi_reversion_signals, GRID_RSI)}`). Saída sempre do MESMO comprimento de `closes`. Tasks 3, 4 e 6 consomem estes nomes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_strategies.py
import pytest

from invest_agent.backtest.strategies import (
    STRATEGIES, Signal, rsi_reversion_signals, sma_cross_signals,
)


def test_sma_cross_gera_entrada_e_saida():
    # fast=2, slow=3. Série sobe e depois despenca:
    closes = [10.0, 10.0, 10.0, 20.0, 30.0, 5.0, 4.0, 3.0]
    out = sma_cross_signals(closes, fast=2, slow=3)
    assert len(out) == len(closes)
    # até idx2 não há SMA(3) anterior definida → HOLD
    assert out[:3] == [0, 0, 0]
    # idx3: fast=(10+20)/2=15 > slow=(10+10+20)/3=13.33 e antes fast==slow → ENTER
    assert out[3] == Signal.ENTER
    # idx5: fast=(30+5)/2=17.5 < slow=(20+30+5)/3=18.33 e antes fast>slow → EXIT
    assert out[5] == Signal.EXIT
    # nunca dois ENTER seguidos sem EXIT no meio
    abertos = 0
    for s in out:
        if s == Signal.ENTER:
            abertos += 1
            assert abertos == 1
        elif s == Signal.EXIT:
            abertos -= 1


def test_sma_cross_fast_deve_ser_menor_que_slow():
    with pytest.raises(ValueError):
        sma_cross_signals([1.0, 2.0], fast=3, slow=2)


def test_rsi_reversion_sai_da_sobrevenda_gera_entrada():
    # queda longa (RSI→0, entra em sobrevenda) seguida de recuperação
    closes = [float(x) for x in range(30, 10, -1)] + [15.0, 20.0, 25.0]
    out = rsi_reversion_signals(closes, period=14, low=30.0, high=70.0)
    assert len(out) == len(closes)
    assert Signal.ENTER in out  # cruzou de <30 para >=30 na recuperação
    idx_enter = out.index(Signal.ENTER)
    assert idx_enter >= 20  # só depois da virada


def test_rsi_reversion_sem_cruzamento_nao_sinaliza():
    closes = [10.0, 10.0, 10.0, 10.0]
    assert rsi_reversion_signals(closes, period=14) == [0, 0, 0, 0]


def test_registry_tem_as_duas_familias_com_grades():
    assert set(STRATEGIES) == {"sma_cross", "rsi_reversion"}
    fn, grid = STRATEGIES["sma_cross"]
    assert fn is sma_cross_signals and len(grid) >= 4
    for params in grid:
        assert params["fast"] < params["slow"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_strategies.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/backtest/strategies.py
"""Geradores de sinal mecânicos para o backtest — proxies determinísticos
do papel do LLM (sem LLM em backtest, spec §5). Sinal no índice i usa
apenas dados até o candle i; a execução acontece na abertura de i+1
(responsabilidade do executor, Tasks 3-4)."""
from __future__ import annotations

from enum import IntEnum

from ..indicators import rsi, sma


class Signal(IntEnum):
    ENTER = 1
    EXIT = -1
    HOLD = 0


def sma_cross_signals(closes: list[float], fast: int, slow: int) -> list[int]:
    """ENTER quando a média rápida cruza para CIMA da lenta; EXIT quando
    cruza para baixo. Mantém no máximo uma posição lógica aberta."""
    if fast >= slow:
        raise ValueError("fast deve ser menor que slow")
    fast_line, slow_line = sma(closes, fast), sma(closes, slow)
    out = [int(Signal.HOLD)] * len(closes)
    in_position = False
    for i in range(1, len(closes)):
        if None in (fast_line[i], slow_line[i],
                    fast_line[i - 1], slow_line[i - 1]):
            continue
        crossed_up = (fast_line[i - 1] <= slow_line[i - 1]
                      and fast_line[i] > slow_line[i])
        crossed_down = (fast_line[i - 1] >= slow_line[i - 1]
                        and fast_line[i] < slow_line[i])
        if crossed_up and not in_position:
            out[i] = int(Signal.ENTER)
            in_position = True
        elif crossed_down and in_position:
            out[i] = int(Signal.EXIT)
            in_position = False
    return out


def rsi_reversion_signals(closes: list[float], period: int = 14,
                          low: float = 30.0, high: float = 70.0) -> list[int]:
    """ENTER quando o RSI cruza de volta para cima do nível de sobrevenda
    (saindo dela); EXIT quando cruza para baixo do nível de sobrecompra."""
    line = rsi(closes, period)
    out = [int(Signal.HOLD)] * len(closes)
    in_position = False
    for i in range(1, len(closes)):
        if line[i] is None or line[i - 1] is None:
            continue
        left_oversold = line[i - 1] < low and line[i] >= low
        left_overbought = line[i - 1] > high and line[i] <= high
        if left_oversold and not in_position:
            out[i] = int(Signal.ENTER)
            in_position = True
        elif left_overbought and in_position:
            out[i] = int(Signal.EXIT)
            in_position = False
    return out


GRID_SMA: list[dict] = [
    {"fast": f, "slow": s}
    for f, s in [(10, 30), (10, 50), (20, 50), (20, 100), (50, 200)]
]

GRID_RSI: list[dict] = [
    {"period": p, "low": lo, "high": hi}
    for p in (7, 14, 21) for lo, hi in [(30.0, 70.0), (20.0, 80.0)]
]

STRATEGIES = {
    "sma_cross": (sma_cross_signals, GRID_SMA),
    "rsi_reversion": (rsi_reversion_signals, GRID_RSI),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_strategies.py -v`
Expected: PASS (5 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/backtest/strategies.py tests/test_strategies.py
git commit -m "feat: sinais mecânicos (SMA cross, RSI reversão) com grades de sweep"
```

---

### Task 3: Sweep de parâmetros em Python puro

**Files:**
- Create: `src/invest_agent/backtest/sweep.py`
- Test: `tests/test_sweep.py`

**Interfaces:**
- Consumes: `Candle` (`invest_agent.data.models`), `CostModel`/`trade_return` (Task 1), `Signal` e assinaturas de gerador de sinais (Task 2).
- Produces: `SweepResult` (dataclass frozen: `params: dict`, `total_return: float`, `n_trades: int`) e `run_sweep(candles: list[Candle], signal_fn, grid: list[dict], costs: CostModel) -> list[SweepResult]` (ordenado por `total_return` desc). Execução simulada SEM look-ahead: sinal no candle i executa na ABERTURA do candle i+1; posição aberta no fim é fechada no close do último candle. Task 6 consome estes nomes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sweep.py
from datetime import datetime, timedelta, timezone

import pytest

from invest_agent.backtest.costs import CostModel, trade_return
from invest_agent.backtest.sweep import SweepResult, run_sweep
from invest_agent.data.models import Candle

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candle(i: int, open_: float, close: float) -> Candle:
    return Candle(symbol="BTCUSDT", interval="1h",
                  open_time=T0 + timedelta(hours=i), open=open_,
                  high=max(open_, close) + 1, low=min(open_, close) - 1,
                  close=close, volume=1.0, quote_volume=1.0, n_trades=1,
                  close_time=T0 + timedelta(hours=i, minutes=59))


# sinal fixo para teste: ENTER no candle 1, EXIT no candle 3
def sinal_fixo(closes: list[float], **params) -> list[int]:
    out = [0] * len(closes)
    out[1], out[3] = 1, -1
    return out


CANDLES = [
    _candle(0, 100.0, 100.0),
    _candle(1, 100.0, 101.0),   # ENTER aqui → executa na abertura do 2
    _candle(2, 102.0, 108.0),
    _candle(3, 108.0, 109.0),   # EXIT aqui → executa na abertura do 4
    _candle(4, 110.0, 111.0),
]


def test_execucao_sem_look_ahead_na_abertura_seguinte():
    costs = CostModel(fee_pct=0.001, slippage_pct=0.0005)
    (res,) = run_sweep(CANDLES, sinal_fixo, [{}], costs)
    # entrada na ABERTURA do candle 2 (102), saída na ABERTURA do 4 (110)
    esperado = trade_return(102.0, 110.0, costs)
    assert res.total_return == pytest.approx(esperado)
    assert res.n_trades == 1


def test_posicao_aberta_no_fim_fecha_no_ultimo_close():
    def so_entra(closes, **p):
        out = [0] * len(closes)
        out[1] = 1
        return out

    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    (res,) = run_sweep(CANDLES, so_entra, [{}], zero)
    # entra na abertura do 2 (102), fecha forçado no close do último (111)
    assert res.total_return == pytest.approx(111.0 / 102.0 - 1)
    assert res.n_trades == 1


def test_ordena_por_retorno_e_carrega_params():
    def parametrizado(closes, ganho=1, **p):
        # ENTER no 0 → executa na abertura do 1
        out = [0] * len(closes)
        out[0] = 1
        return out

    subida = [_candle(0, 100.0, 100.0), _candle(1, 100.0, 100.0),
              _candle(2, 100.0, 150.0)]
    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    # duas combinações idênticas em sinal → mesmo retorno; garante que
    # params sobrevivem no resultado e a ordenação é estável/desc
    results = run_sweep(subida, parametrizado,
                        [{"ganho": 1}, {"ganho": 2}], zero)
    assert [r.params for r in results] == [{"ganho": 1}, {"ganho": 2}]
    assert all(r.total_return == pytest.approx(0.5) for r in results)


def test_sinal_no_ultimo_candle_e_ignorado():
    def entra_no_fim(closes, **p):
        out = [0] * len(closes)
        out[-1] = 1
        return out

    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    (res,) = run_sweep(CANDLES, entra_no_fim, [{}], zero)
    assert res.n_trades == 0 and res.total_return == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sweep.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/backtest/sweep.py
"""Varredura de parâmetros em Python puro — o filtro grosseiro que a spec
delegava ao vectorbt (substituído por ruling: llvmlite não compila nesta
máquina; papel idêntico). Execução simulada sem look-ahead: sinal no
candle i executa na ABERTURA do candle i+1. O resultado alimenta o estágio
de fills realistas (backtrader, Task 4)."""
from __future__ import annotations

from dataclasses import dataclass

from ..data.models import Candle
from .costs import CostModel, trade_return
from .strategies import Signal


@dataclass(frozen=True)
class SweepResult:
    params: dict
    total_return: float
    n_trades: int


def _simulate(candles: list[Candle], signals: list[int],
              costs: CostModel) -> tuple[float, int]:
    equity = 1.0
    n_trades = 0
    entry_price: float | None = None
    for i in range(len(candles) - 1):  # sinal no último candle não executa
        next_open = candles[i + 1].open
        if signals[i] == Signal.ENTER and entry_price is None:
            entry_price = next_open
        elif signals[i] == Signal.EXIT and entry_price is not None:
            equity *= 1 + trade_return(entry_price, next_open, costs)
            entry_price = None
            n_trades += 1
    if entry_price is not None:  # fecha posição pendente no último close
        equity *= 1 + trade_return(entry_price, candles[-1].close, costs)
        n_trades += 1
    return equity - 1, n_trades


def run_sweep(candles: list[Candle], signal_fn, grid: list[dict],
              costs: CostModel) -> list[SweepResult]:
    closes = [c.close for c in candles]
    results = []
    for params in grid:
        signals = signal_fn(closes, **params)
        total, n_trades = _simulate(candles, signals, costs)
        results.append(SweepResult(params=params, total_return=total,
                                   n_trades=n_trades))
    return sorted(results, key=lambda r: r.total_return, reverse=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sweep.py -v`
Expected: PASS (4 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/backtest/sweep.py tests/test_sweep.py
git commit -m "feat: sweep de parâmetros puro, sem look-ahead, com custos"
```

---

### Task 4: Fills realistas com backtrader

**Files:**
- Modify: `pyproject.toml` (adicionar `backtrader>=1.9.78` às dependencies)
- Create: `src/invest_agent/backtest/engine_bt.py`
- Test: `tests/test_engine_bt.py`

**Interfaces:**
- Consumes: `Candle` (Fase 1a), sinais na convenção da Task 2 (lista de int alinhada aos candles).
- Produces: `BacktestRun` (dataclass frozen: `initial_cash: float`, `final_value: float`, `n_trades: int`, `equity_curve: list[float]`) e `run_backtrader(candles: list[Candle], signals: list[int], costs: CostModel, initial_cash: float = 10_000.0, stake_pct: float = 0.99) -> BacktestRun`. Ordens a mercado executam na ABERTURA do candle seguinte (comportamento default do backtrader — é exatamente o "fills realistas" da spec); comissão percentual = `costs.fee_pct`; slippage percentual = `costs.slippage_pct`. Task 6 consome estes nomes.

- [ ] **Step 0: Instalar e fixar a dependência**

```bash
python3 -m pip install "backtrader>=1.9.78"
```

Em `pyproject.toml`, trocar a linha de dependencies por:

```toml
dependencies = ["duckdb>=1.1", "pyarrow>=17", "backtrader>=1.9.78"]
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine_bt.py
from datetime import datetime, timedelta, timezone

import pytest

from invest_agent.backtest.costs import CostModel
from invest_agent.backtest.engine_bt import run_backtrader
from invest_agent.data.models import Candle

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candle(i: int, open_: float, close: float) -> Candle:
    return Candle(symbol="BTCUSDT", interval="1d",
                  open_time=T0 + timedelta(days=i), open=open_,
                  high=max(open_, close) + 1, low=min(open_, close) - 1,
                  close=close, volume=1.0, quote_volume=1.0, n_trades=1,
                  close_time=T0 + timedelta(days=i, hours=23))


CANDLES = [
    _candle(0, 100.0, 100.0),
    _candle(1, 102.0, 104.0),   # fill da entrada acontece AQUI (abertura 102)
    _candle(2, 106.0, 108.0),
    _candle(3, 105.0, 107.0),   # fill da saída AQUI (abertura 105)
    _candle(4, 107.0, 109.0),
]
# ENTER no candle 0, EXIT no candle 2:
SIGNALS = [1, 0, -1, 0, 0]


def test_fill_na_abertura_seguinte_sem_look_ahead():
    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    run = run_backtrader(CANDLES, SIGNALS, zero, initial_cash=10_000.0,
                         stake_pct=0.99)
    # sizing usa o CLOSE do candle do sinal (100) com margem de 5%:
    # size = int(10000*0.99 / (100*1.05)) = 94 unidades
    # compra fill na abertura do candle 1 (102); venda na abertura do 3 (105)
    # lucro = 94 * (105 - 102) = 282
    assert run.n_trades == 1
    assert run.final_value == pytest.approx(10_000.0 + 94 * 3.0)


def test_comissao_percentual_reduz_o_resultado():
    costs = CostModel(fee_pct=0.001, slippage_pct=0.0)
    run = run_backtrader(CANDLES, SIGNALS, costs, initial_cash=10_000.0,
                         stake_pct=0.99)
    # mesmas 94 unidades; comissões: 94*102*0.001 + 94*105*0.001
    esperado = 10_000.0 + 94 * 3.0 - 94 * 102 * 0.001 - 94 * 105 * 0.001
    assert run.final_value == pytest.approx(esperado)


def test_slippage_percentual_piora_os_fills():
    costs = CostModel(fee_pct=0.0, slippage_pct=0.01)
    run = run_backtrader(CANDLES, SIGNALS, costs, initial_cash=10_000.0,
                         stake_pct=0.99)
    # sizing não muda (usa o close do sinal): 94 unidades
    # compra fill = 102*1.01 = 103.02; venda fill = 105*0.99 = 103.95
    esperado = 10_000.0 + 94 * (105 * 0.99 - 102 * 1.01)
    assert run.final_value == pytest.approx(esperado)


def test_equity_curve_tem_um_ponto_por_candle():
    zero = CostModel(fee_pct=0.0, slippage_pct=0.0)
    run = run_backtrader(CANDLES, SIGNALS, zero)
    assert len(run.equity_curve) == len(CANDLES)
    assert run.equity_curve[0] == pytest.approx(10_000.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_engine_bt.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'invest_agent.backtest.engine_bt'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/backtest/engine_bt.py
"""Estágio de fills realistas (spec §5): backtrader executa as ordens a
mercado na ABERTURA do candle seguinte ao sinal — sem look-ahead e sem
cheat-on-close. Comissão e slippage percentuais vêm do CostModel."""
from __future__ import annotations

from dataclasses import dataclass

import backtrader as bt

from ..data.models import Candle
from .costs import CostModel
from .strategies import Signal


@dataclass(frozen=True)
class BacktestRun:
    initial_cash: float
    final_value: float
    n_trades: int
    equity_curve: list[float]


class _CandleData(bt.feeds.DataBase):
    """Feed que serve diretamente a lista de Candle (sem CSV/pandas).
    Usa o sistema de params do backtrader (idioma canônico p/ feeds)."""

    params = (("candles", None),)

    def start(self):
        super().start()
        self._idx = 0

    def _load(self) -> bool:
        if self._idx >= len(self.p.candles):
            return False
        c = self.p.candles[self._idx]
        self._idx += 1
        self.lines.datetime[0] = bt.date2num(c.open_time.replace(tzinfo=None))
        self.lines.open[0] = c.open
        self.lines.high[0] = c.high
        self.lines.low[0] = c.low
        self.lines.close[0] = c.close
        self.lines.volume[0] = c.volume
        self.lines.openinterest[0] = 0.0
        return True


class _SignalStrategy(bt.Strategy):
    params = (("signals", None), ("stake_pct", 0.99))

    def __init__(self):
        self._bar = 0
        self.closed_trades = 0
        self.equity_curve: list[float] = []

    def next(self):
        self.equity_curve.append(self.broker.getvalue())
        signal = self.p.signals[self._bar]
        self._bar += 1
        if signal == Signal.ENTER and not self.position:
            cash = self.broker.getcash() * self.p.stake_pct
            price = self.data.close[0]
            size = int(cash / (price * 1.05))  # margem p/ gap+slippage
            if size > 0:
                self.buy(size=size)
        elif signal == Signal.EXIT and self.position:
            self.close()

    def notify_trade(self, trade):
        if trade.isclosed:
            self.closed_trades += 1


def run_backtrader(candles: list[Candle], signals: list[int],
                   costs: CostModel, initial_cash: float = 10_000.0,
                   stake_pct: float = 0.99) -> BacktestRun:
    if len(candles) != len(signals):
        raise ValueError("candles e signals devem ter o mesmo comprimento")
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=costs.fee_pct)
    if costs.slippage_pct:
        cerebro.broker.set_slippage_perc(
            perc=costs.slippage_pct, slip_open=True, slip_match=True,
            slip_out=True)
    cerebro.adddata(_CandleData(candles=candles))
    cerebro.addstrategy(_SignalStrategy, signals=signals,
                        stake_pct=stake_pct)
    (strat,) = cerebro.run()
    return BacktestRun(initial_cash=initial_cash,
                       final_value=cerebro.broker.getvalue(),
                       n_trades=strat.closed_trades,
                       equity_curve=strat.equity_curve)
```

**Nota para o implementador:** os valores esperados dos testes assumem a mecânica default do backtrader: ordem a mercado criada no candle i executa na ABERTURA do candle i+1; `setcommission(commission=X)` sem margin é comissão PERCENTUAL sobre `size*preço`, cobrada nos dois lados; `set_slippage_perc(..., slip_open=True, slip_out=True)` aplica slippage também em fills por abertura e permite preço fora do range do candle. O sizing usa o CLOSE do candle do sinal com margem de 5% (`int(cash*stake_pct / (close*1.05))`) para o fill do candle seguinte nunca faltar caixa — nos três primeiros testes isso dá exatamente 94 unidades. Se algum valor não bater ao rodar, PARE e reporte NEEDS_CONTEXT com o número observado (size real via `trade.size`, preço de fill via `order.executed.price`); NÃO "faça o teste passar" mudando semântica nem valores esperados por conta própria.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_engine_bt.py -v`
Expected: PASS (4 testes). Se falhar por diferença de sizing/fill, siga a nota acima: reporte NEEDS_CONTEXT com os números observados em vez de dobrar a semântica.

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest`
Expected: tudo verde, output limpo.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/invest_agent/backtest/engine_bt.py tests/test_engine_bt.py
git commit -m "feat: fills realistas via backtrader (abertura seguinte, comissão, slippage)"
```

---

### Task 5: Relatório em português

**Files:**
- Create: `src/invest_agent/backtest/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `BacktestRun` (Task 4), `CostModel`/`buy_and_hold_return`/`max_drawdown` (Task 1), `SweepResult` (Task 3).
- Produces: `build_report(symbol: str, interval: str, params: dict, run: BacktestRun, candles: list[Candle], costs: CostModel, sweep: list[SweepResult] | None = None) -> str` — texto em português com: retorno da estratégia, retorno buy-and-hold (mesmos custos), max drawdown da estratégia, nº de trades, veredito explícito ("SUPERA"/"NÃO SUPERA o buy-and-hold após custos"), e top-5 do sweep quando fornecido. Task 6 consome este nome.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
from datetime import datetime, timedelta, timezone

from invest_agent.backtest.costs import CostModel, buy_and_hold_return
from invest_agent.backtest.engine_bt import BacktestRun
from invest_agent.backtest.report import build_report
from invest_agent.backtest.sweep import SweepResult
from invest_agent.data.models import Candle

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candle(i: int, open_: float, close: float) -> Candle:
    return Candle(symbol="BTCUSDT", interval="1d",
                  open_time=T0 + timedelta(days=i), open=open_,
                  high=max(open_, close), low=min(open_, close),
                  close=close, volume=1.0, quote_volume=1.0, n_trades=1,
                  close_time=T0 + timedelta(days=i, hours=23))


CANDLES = [_candle(0, 100.0, 105.0), _candle(1, 105.0, 110.0)]
ZERO = CostModel(fee_pct=0.0, slippage_pct=0.0)


def test_report_estrategia_que_supera():
    run = BacktestRun(initial_cash=10_000.0, final_value=12_000.0,
                      n_trades=3, equity_curve=[10_000.0, 11_000.0, 12_000.0])
    texto = build_report("BTCUSDT", "1d", {"fast": 10, "slow": 30}, run,
                         CANDLES, ZERO)
    assert "BTCUSDT" in texto and "1d" in texto
    assert "+20.00%" in texto           # retorno da estratégia
    assert "+10.00%" in texto           # buy-and-hold: 100 → 110 sem custos
    assert "SUPERA o buy-and-hold" in texto
    assert "3" in texto                 # nº de trades
    assert "fast" in texto              # params visíveis


def test_report_estrategia_que_nao_supera():
    run = BacktestRun(initial_cash=10_000.0, final_value=10_100.0,
                      n_trades=1, equity_curve=[10_000.0, 10_100.0])
    texto = build_report("BTCUSDT", "1d", {}, run, CANDLES, ZERO)
    assert "NÃO SUPERA o buy-and-hold" in texto


def test_report_inclui_drawdown_e_top_sweep():
    run = BacktestRun(initial_cash=10_000.0, final_value=10_500.0,
                      n_trades=2, equity_curve=[10_000.0, 12_000.0, 9_000.0,
                                                10_500.0])
    sweep = [SweepResult(params={"fast": 10, "slow": 30},
                         total_return=0.30, n_trades=4),
             SweepResult(params={"fast": 20, "slow": 50},
                         total_return=0.10, n_trades=2)]
    texto = build_report("BTCUSDT", "1d", {"fast": 10, "slow": 30}, run,
                         CANDLES, ZERO, sweep=sweep)
    assert "25.00%" in texto            # drawdown (12000→9000)
    assert "+30.00%" in texto and "+10.00%" in texto  # linhas do sweep
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_report.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/backtest/report.py
"""Relatório do backtest em português: estratégia vs buy-and-hold com os
MESMOS custos (o critério de saída da Fase 1, spec §5)."""
from __future__ import annotations

from ..data.models import Candle
from .costs import CostModel, buy_and_hold_return, max_drawdown
from .engine_bt import BacktestRun
from .sweep import SweepResult


def _pct(value: float) -> str:
    return f"{value:+.2%}".replace("%", "%")


def build_report(symbol: str, interval: str, params: dict,
                 run: BacktestRun, candles: list[Candle], costs: CostModel,
                 sweep: list[SweepResult] | None = None) -> str:
    strategy_return = run.final_value / run.initial_cash - 1
    baseline = buy_and_hold_return(candles[0].open, candles[-1].close, costs)
    drawdown = max_drawdown(run.equity_curve)
    periodo = (f"{candles[0].open_time:%Y-%m-%d} a "
               f"{candles[-1].close_time:%Y-%m-%d}")
    veredito = ("SUPERA o buy-and-hold após custos"
                if strategy_return > baseline
                else "NÃO SUPERA o buy-and-hold após custos")
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
    ]
    if sweep:
        linhas += ["", "## Top do sweep (retorno bruto simulado)"]
        for r in sweep[:5]:
            linhas.append(f"- {r.params}: {r.total_return:+.2%} "
                          f"({r.n_trades} trades)")
    return "\n".join(linhas)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_report.py -v`
Expected: PASS (3 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/backtest/report.py tests/test_report.py
git commit -m "feat: relatório do backtest vs buy-and-hold em português"
```

---

### Task 6: CLI do backtest

**Files:**
- Create: `src/invest_agent/backtest/run.py`
- Test: `tests/test_backtest_run.py`
- Modify: `README.md` (seção de uso do backtest)

**Interfaces:**
- Consumes: `CandleStore` (Fase 1a), `STRATEGIES` (Task 2), `run_sweep` (Task 3), `run_backtrader` (Task 4), `build_report` (Task 5), `CostModel` (Task 1).
- Produces: `backtest_symbol(store, symbol, interval, strategy_name, costs, start=None, end=None, initial_cash=10_000.0) -> str` (sweep → melhores params → backtrader → relatório) e `main(argv)` executável via `python3 -m invest_agent.backtest.run`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_run.py
from datetime import datetime, timedelta, timezone

import pytest

from invest_agent.backtest.costs import CostModel
from invest_agent.backtest.run import backtest_symbol
from invest_agent.data.models import Candle
from invest_agent.data.store import CandleStore

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candle(i: int, close: float) -> Candle:
    open_ = close  # candles "chatos": open == close, sem ruído
    return Candle(symbol="BTCUSDT", interval="1h",
                  open_time=T0 + timedelta(hours=i), open=open_,
                  high=close + 0.5, low=close - 0.5, close=close,
                  volume=1.0, quote_volume=1.0, n_trades=1,
                  close_time=T0 + timedelta(hours=i, minutes=59))


def _make_store(tmp_path) -> CandleStore:
    store = CandleStore(tmp_path)
    # 250 candles: sobe, cai, sobe — o suficiente para cruzamentos de SMA
    closes = ([100.0 + i for i in range(100)]           # 100→199
              + [199.0 - 2 * i for i in range(50)]      # 199→101
              + [101.0 + i for i in range(100)])        # 101→200
    store.append([_candle(i, c) for i, c in enumerate(closes)])
    return store


def test_backtest_symbol_produz_relatorio_completo(tmp_path):
    store = _make_store(tmp_path)
    texto = backtest_symbol(store, "BTCUSDT", "1h", "sma_cross",
                            CostModel())
    assert "Backtest BTCUSDT 1h" in texto
    assert "buy-and-hold" in texto
    assert "Veredito:" in texto
    assert "Top do sweep" in texto


def test_backtest_symbol_estrategia_desconhecida(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="estratégia desconhecida"):
        backtest_symbol(store, "BTCUSDT", "1h", "nao_existe", CostModel())


def test_backtest_symbol_sem_candles(tmp_path):
    store = CandleStore(tmp_path)
    with pytest.raises(ValueError, match="sem candles"):
        backtest_symbol(store, "BTCUSDT", "1h", "sma_cross", CostModel())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_backtest_run.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/backtest/run.py
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
                    initial_cash: float = 10_000.0) -> str:
    if strategy_name not in STRATEGIES:
        raise ValueError(f"estratégia desconhecida: {strategy_name} "
                         f"(disponíveis: {', '.join(sorted(STRATEGIES))})")
    candles = store.read(symbol, interval, start=start, end=end)
    if not candles:
        raise ValueError(f"sem candles para {symbol} {interval} no store — "
                         "rode a ingestão primeiro")
    signal_fn, grid = STRATEGIES[strategy_name]
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
    args = parser.parse_args(argv)

    costs = CostModel(fee_pct=args.fee, slippage_pct=args.slippage)
    store = CandleStore(args.root)
    print(backtest_symbol(store, args.symbol, args.interval, args.strategy,
                          costs, start=args.start, end=args.end,
                          initial_cash=args.cash))


if __name__ == "__main__":
    main()
```

No `README.md`, acrescentar após a seção "📥 Ingestão de candles (Fase 1)":

```markdown
## 📊 Backtest vs buy-and-hold (Fase 1)

```bash
python3 -m invest_agent.backtest.run --symbol BTCUSDT --interval 1h --strategy sma_cross
```

Sweep de parâmetros em Python puro → fills realistas no backtrader (ordem
executa na abertura do candle seguinte, comissão 0,10% + slippage 0,05%
por lado) → relatório comparando com buy-and-hold sob os mesmos custos.
Estratégias mecânicas apenas — sem LLM em backtest (janela antiga já
esteve no treino do modelo; um LLM "acertando" ali é look-ahead, não edge).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_backtest_run.py -v`
Expected: PASS (3 testes). Suíte completa: `python3 -m pytest` — tudo verde.

- [ ] **Step 5: Smoke test manual (se houver dados reais no store)**

Run: `python3 -m invest_agent.backtest.run --symbol BTCUSDT --interval 1h 2>/dev/null || echo "sem dados reais — ok, testes unitários são o gate"`
Expected: relatório impresso, ou a mensagem de fallback (a ingestão real pode não ter rodado nesta máquina por falta de certificados SSL).

- [ ] **Step 6: Commit**

```bash
git add src/invest_agent/backtest/run.py tests/test_backtest_run.py README.md
git commit -m "feat: CLI de backtest — sweep + backtrader + relatório vs buy-and-hold"
```

---

## Self-review do plano (executada na escrita)

- **Cobertura da spec (escopo 1b):** custos 0,10% + slippage → T1 (CostModel, defaults exatos); varredura → T3 (ruling: Python puro no lugar do vectorbt, registrado no cabeçalho e no ledger); fills realistas backtrader → T4; backtest honesto vs buy-and-hold → T1 (baseline) + T5 (comparação e veredito) + T6 (composição); "sem LLM em backtest" → constraint global + nota no README (T6).
- **Placeholders:** nenhum TBD/TODO; todo step tem código completo. A nota da Task 4 dá o protocolo de divergência (NEEDS_CONTEXT com números observados) em vez de deixar o implementador "fazer passar".
- **Consistência de tipos:** `CostModel(fee_pct, slippage_pct)` idêntico em T1/T3/T4/T5/T6; `Signal` IntEnum comparável a int em T2/T3/T4; `SweepResult(params, total_return, n_trades)` em T3/T5/T6; `BacktestRun(initial_cash, final_value, n_trades, equity_curve)` em T4/T5/T6; `run_sweep(candles, signal_fn, grid, costs)` em T3/T6; `build_report(symbol, interval, params, run, candles, costs, sweep=None)` em T5/T6; `STRATEGIES: nome → (fn, grid)` em T2/T6.
- **Risco conhecido (registrado):** os valores esperados nos testes da Task 4 dependem da mecânica interna do backtrader (fill na abertura seguinte, base de cálculo do sizing). O protocolo em caso de divergência está na nota da task: reportar NEEDS_CONTEXT com números observados, nunca ajustar semântica para "passar".
