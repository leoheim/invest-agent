# Fase 0 — Motor de Regras: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir a fundação do agente de investimento: modelos de domínio + motor de regras determinístico completo, com testes, sem nenhuma chamada de rede ou LLM.

**Architecture:** Biblioteca Python pura (`src/invest_agent/`). O LLM (fases futuras) produzirá `Proposal`; o `RulesEngine` avalia a proposta contra o estado do portfólio e do mercado e devolve um `Verdict` (aprovada / rejeitada / precisa de aprovação humana) contendo, quando aprovada, uma `OrderIntent` com sizing calculado por código e stop-loss obrigatório. Tudo é função pura ou classe sem I/O — exceto o kill switch, que é um arquivo em disco de propósito (sobrevive à morte do processo).

**Tech Stack:** Python ≥3.12, stdlib apenas (dataclasses, enum, hashlib, datetime, pathlib). `pytest` como única dependência de dev. Sem pydantic, sem rede, sem SQLite nesta fase.

**Spec:** `docs/superpowers/specs/2026-09-05-invest-agent-design.md` (seções 3, 4.4 e decisões da seção 2)

## Global Constraints

- Python ≥ 3.12; somente stdlib em runtime; `pytest` só em dev.
- Nenhum módulo desta fase faz rede, chama LLM ou lê variáveis de ambiente.
- Todas as datas/horas em UTC (`datetime.now(timezone.utc)` sempre recebido como parâmetro `now`, nunca chamado dentro da lógica — determinismo para testes).
- Perfil de risco **moderado** (valores exatos da spec): máx 10%/ativo, máx 60% investido, halt a −5% dia / −10% semana / −15% mês, ≤4 ordens/dia, cooldown 4h/ativo, HITL >2% do capital, stop-loss em toda entrada.
- Mensagens de motivo de rejeição em português, uma por regra violada (vão direto para o Telegram na Fase 2).
- Commits pequenos e frequentes; mensagens concisas, sem assinatura.

## Planos futuros (fora deste documento)

Fase 1: ingestão + backtest · Fase 2a: adapter Binance testnet + reconciliação · Fase 2b: bot Telegram + HITL · Fase 2c: cérebro Claude + loop. Cada um terá seu próprio plano após a execução deste.

---

### Task 1: Scaffold do projeto + modelos de domínio

**Files:**
- Create: `pyproject.toml`
- Create: `src/invest_agent/__init__.py`
- Create: `src/invest_agent/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nada (primeira task)
- Produces (usado por TODAS as tasks seguintes):
  - `Action` (Enum): `BUY | SELL | CLOSE | HOLD`
  - `Proposal(symbol: str, action: Action, conviction: float, rationale: str, cycle_id: str)` — frozen; valida `0.0 <= conviction <= 1.0` no `__post_init__`
  - `Position(symbol: str, qty: float, avg_price: float)` com método `notional(price: float) -> float`
  - `MarketSnapshot(symbol: str, last_price: float, best_bid: float, best_ask: float, candle_age_seconds: float, quote_volume_24h: float)`
  - `PortfolioState(equity: float, cash: float, positions: dict[str, Position], orders_today: int, last_order_at: dict[str, datetime])`
  - `OrderIntent(symbol: str, side: str, qty: float, limit_price: float, stop_loss_price: float | None, client_order_id: str)`
  - `VerdictStatus` (Enum): `APPROVED | REJECTED | NEEDS_APPROVAL`
  - `Verdict(status: VerdictStatus, reasons: list[str], order: OrderIntent | None)`

- [ ] **Step 1: Criar o esqueleto do projeto**

`pyproject.toml`:

```toml
[project]
name = "invest-agent"
version = "0.1.0"
description = "Agente de investimento pessoal: LLM propõe, código dispõe"
requires-python = ">=3.12"
dependencies = []

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

`src/invest_agent/__init__.py` — arquivo vazio.

- [ ] **Step 2: Escrever os testes que falham**

`tests/test_models.py`:

```python
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
```

- [ ] **Step 3: Rodar e confirmar que falham**

Run: `cd ~/Documents/invest-agent && python3 -m pytest tests/test_models.py -v`
Expected: FAIL/ERROR com `ModuleNotFoundError: No module named 'invest_agent.models'`
(Se `pytest` não estiver instalado: `python3 -m pip install pytest`.)

- [ ] **Step 4: Implementar os modelos**

`src/invest_agent/models.py`:

```python
"""Modelos de domínio. Frozen dataclasses: o estado nunca é mutado em
lugar nenhum — cada ciclo constrói snapshots novos."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"
    HOLD = "hold"


@dataclass(frozen=True)
class Proposal:
    """O que o LLM produz. Note o que NÃO está aqui: preço e quantidade —
    sizing é responsabilidade exclusiva do código."""
    symbol: str
    action: Action
    conviction: float
    rationale: str
    cycle_id: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.conviction <= 1.0:
            raise ValueError(f"conviction fora de 0..1: {self.conviction}")


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_price: float

    def notional(self, price: float) -> float:
        return self.qty * price


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    last_price: float
    best_bid: float
    best_ask: float
    candle_age_seconds: float
    quote_volume_24h: float


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    orders_today: int = 0
    last_order_at: dict[str, datetime] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str  # "BUY" | "SELL"
    qty: float
    limit_price: float
    stop_loss_price: float | None
    client_order_id: str


class VerdictStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_APPROVAL = "needs_approval"


@dataclass(frozen=True)
class Verdict:
    status: VerdictStatus
    reasons: list[str]
    order: OrderIntent | None
```

- [ ] **Step 5: Rodar e confirmar que passam**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "feat: scaffold + modelos de domínio (Proposal, Verdict, OrderIntent)"
```

---

### Task 2: Perfil de risco (config)

**Files:**
- Create: `src/invest_agent/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nada
- Produces: `RiskProfile` (frozen dataclass) e a constante `MODERADO: RiskProfile`. Campos exatos:
  `name: str`, `max_position_pct: float`, `max_exposure_pct: float`, `stop_loss_pct: float`, `max_orders_per_day: int`, `cooldown_hours: float`, `hitl_threshold_pct: float`, `daily_loss_halt_pct: float`, `weekly_loss_halt_pct: float`, `monthly_loss_halt_pct: float`, `max_spread_pct: float`, `max_candle_age_seconds: float`, `min_notional_usdt: float`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_config.py`:

```python
from invest_agent.config import MODERADO


def test_perfil_moderado_bate_com_a_spec():
    assert MODERADO.max_position_pct == 0.10
    assert MODERADO.max_exposure_pct == 0.60
    assert MODERADO.daily_loss_halt_pct == 0.05
    assert MODERADO.weekly_loss_halt_pct == 0.10
    assert MODERADO.monthly_loss_halt_pct == 0.15
    assert MODERADO.max_orders_per_day == 4
    assert MODERADO.cooldown_hours == 4.0
    assert MODERADO.hitl_threshold_pct == 0.02
    assert MODERADO.stop_loss_pct > 0  # stop obrigatório, valor > 0
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar**

`src/invest_agent/config.py`:

```python
"""Perfis de risco. Valores do perfil moderado travados na spec
(docs/superpowers/specs/2026-09-05-invest-agent-design.md, seção 2).
SÓ um humano edita este arquivo — nenhum caminho de código escreve aqui."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskProfile:
    name: str
    max_position_pct: float
    max_exposure_pct: float
    stop_loss_pct: float
    max_orders_per_day: int
    cooldown_hours: float
    hitl_threshold_pct: float
    daily_loss_halt_pct: float
    weekly_loss_halt_pct: float
    monthly_loss_halt_pct: float
    max_spread_pct: float
    max_candle_age_seconds: float
    min_notional_usdt: float


MODERADO = RiskProfile(
    name="moderado",
    max_position_pct=0.10,
    max_exposure_pct=0.60,
    stop_loss_pct=0.05,
    max_orders_per_day=4,
    cooldown_hours=4.0,
    hitl_threshold_pct=0.02,
    daily_loss_halt_pct=0.05,
    weekly_loss_halt_pct=0.10,
    monthly_loss_halt_pct=0.15,
    max_spread_pct=0.005,
    max_candle_age_seconds=600.0,
    min_notional_usdt=10.0,
)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/config.py tests/test_config.py
git commit -m "feat: perfil de risco moderado (valores da spec)"
```

---

### Task 3: Whitelist dinâmica por critérios

**Files:**
- Create: `src/invest_agent/whitelist.py`
- Test: `tests/test_whitelist.py`

**Interfaces:**
- Consumes: nada dos módulos anteriores
- Produces:
  - `SymbolStats(symbol: str, base: str, quote: str, quote_volume_30d: float, listed_days: int, is_leveraged: bool)` (frozen dataclass)
  - `build_whitelist(stats: list[SymbolStats], size: int = 20) -> frozenset[str]`
  - Constantes `ALWAYS_INCLUDED: frozenset[str]` (= `{"BTCUSDT", "ETHUSDT"}`) e `STABLECOIN_BASES: frozenset[str]`
  - Na Fase 2, um job semanal alimentará `stats` com dados reais da Binance; aqui é função pura.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_whitelist.py`:

```python
from invest_agent.whitelist import SymbolStats, build_whitelist, ALWAYS_INCLUDED


def _s(symbol, base, vol, days=400, lev=False, quote="USDT"):
    return SymbolStats(symbol=symbol, base=base, quote=quote,
                       quote_volume_30d=vol, listed_days=days,
                       is_leveraged=lev)


def test_btc_e_eth_sempre_entram_mesmo_sem_stats():
    assert ALWAYS_INCLUDED <= build_whitelist([])


def test_seleciona_top_por_volume():
    stats = [_s("SOLUSDT", "SOL", 900), _s("XRPUSDT", "XRP", 800),
             _s("DOGEUSDT", "DOGE", 100)]
    wl = build_whitelist(stats, size=2)
    assert "SOLUSDT" in wl and "XRPUSDT" in wl
    assert "DOGEUSDT" not in wl


def test_exclui_stablecoins_alavancados_novos_e_nao_usdt():
    stats = [
        _s("USDCUSDT", "USDC", 9999),            # stablecoin
        _s("BTCUPUSDT", "BTCUP", 9999, lev=True), # token alavancado
        _s("NEWUSDT", "NEW", 9999, days=30),      # listado há <1 ano
        _s("SOLBRL", "SOL", 9999, quote="BRL"),   # par não-USDT
        _s("SOLUSDT", "SOL", 500),
    ]
    wl = build_whitelist(stats, size=20)
    assert wl - ALWAYS_INCLUDED == {"SOLUSDT"}
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `python3 -m pytest tests/test_whitelist.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar**

`src/invest_agent/whitelist.py`:

```python
"""Whitelist dinâmica: o agente escolhe livremente DENTRO desta lista;
ticker fora dela é rejeitado pelo motor. Recalculada por código (job
semanal na Fase 2) — o LLM nunca influencia os critérios."""
from __future__ import annotations

from dataclasses import dataclass

ALWAYS_INCLUDED: frozenset[str] = frozenset({"BTCUSDT", "ETHUSDT"})

STABLECOIN_BASES: frozenset[str] = frozenset({
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "BUSD", "USDP", "PYUSD", "EURI",
})

MIN_LISTED_DAYS = 365


@dataclass(frozen=True)
class SymbolStats:
    symbol: str
    base: str
    quote: str
    quote_volume_30d: float
    listed_days: int
    is_leveraged: bool


def build_whitelist(stats: list[SymbolStats], size: int = 20) -> frozenset[str]:
    eligible = [
        s for s in stats
        if s.quote == "USDT"
        and s.base not in STABLECOIN_BASES
        and not s.is_leveraged
        and s.listed_days >= MIN_LISTED_DAYS
    ]
    top = sorted(eligible, key=lambda s: s.quote_volume_30d, reverse=True)[:size]
    return ALWAYS_INCLUDED | {s.symbol for s in top}
```

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python3 -m pytest tests/test_whitelist.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/whitelist.py tests/test_whitelist.py
git commit -m "feat: whitelist dinâmica por critérios (top-N USDT, sem stablecoins/alavancados)"
```

---

### Task 4: Sizing por código

**Files:**
- Create: `src/invest_agent/sizing.py`
- Test: `tests/test_sizing.py`

**Interfaces:**
- Consumes: `RiskProfile` (Task 2)
- Produces: `compute_buy_qty(equity: float, price: float, conviction: float, current_position_notional: float, total_invested_notional: float, profile: RiskProfile) -> float` — retorna a quantidade a comprar (0.0 se não couber nada respeitando os tetos). Regra: notional-alvo = `equity * max_position_pct * conviction`, cortado pelo espaço restante no teto por ativo E no teto de exposição total; abaixo de `min_notional_usdt` → 0.0.

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_sizing.py`:

```python
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
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `python3 -m pytest tests/test_sizing.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar**

`src/invest_agent/sizing.py`:

```python
"""Sizing é 100% código. A convicção do LLM apenas MODULA o tamanho
dentro do teto — nunca o define. Aritmética de posição em LLM é fonte
garantida de erro (ver pesquisa, relatório de arquitetura §3.3)."""
from __future__ import annotations

from invest_agent.config import RiskProfile


def compute_buy_qty(
    equity: float,
    price: float,
    conviction: float,
    current_position_notional: float,
    total_invested_notional: float,
    profile: RiskProfile,
) -> float:
    if price <= 0 or equity <= 0:
        return 0.0

    target = equity * profile.max_position_pct * conviction
    room_asset = equity * profile.max_position_pct - current_position_notional
    room_total = equity * profile.max_exposure_pct - total_invested_notional
    notional = min(target, room_asset, room_total)

    if notional < profile.min_notional_usdt:
        return 0.0
    return notional / price
```

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python3 -m pytest tests/test_sizing.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/sizing.py tests/test_sizing.py
git commit -m "feat: sizing por código com tetos por ativo e exposição total"
```

---

### Task 5: Gates de mercado e de frequência

**Files:**
- Create: `src/invest_agent/gates.py`
- Test: `tests/test_gates.py`

**Interfaces:**
- Consumes: `MarketSnapshot`, `PortfolioState` (Task 1), `RiskProfile` (Task 2)
- Produces (cada gate retorna `list[str]` de violações; lista vazia = passou):
  - `check_market_quality(market: MarketSnapshot, profile: RiskProfile) -> list[str]` — dado fresco (candle ≤ max age), book são (`0 < best_bid <= best_ask`), spread relativo ≤ máx, volume 24h > 0
  - `check_frequency(symbol: str, portfolio: PortfolioState, profile: RiskProfile, now: datetime) -> list[str]` — ordens no dia < máx; cooldown por símbolo respeitado

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_gates.py`:

```python
from datetime import datetime, timedelta, timezone

from invest_agent.config import MODERADO
from invest_agent.gates import check_frequency, check_market_quality
from invest_agent.models import MarketSnapshot, PortfolioState

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def _mkt(**kw):
    base = dict(symbol="BTCUSDT", last_price=100_000.0, best_bid=99_990.0,
                best_ask=100_010.0, candle_age_seconds=60.0,
                quote_volume_24h=1e9)
    base.update(kw)
    return MarketSnapshot(**base)


def test_mercado_saudavel_passa():
    assert check_market_quality(_mkt(), MODERADO) == []


def test_dado_velho_rejeita():
    viol = check_market_quality(_mkt(candle_age_seconds=999.0), MODERADO)
    assert any("dado de mercado velho" in v for v in viol)


def test_spread_largo_rejeita():
    viol = check_market_quality(
        _mkt(best_bid=99_000.0, best_ask=101_000.0), MODERADO)
    assert any("spread" in v for v in viol)


def test_book_invertido_rejeita():
    viol = check_market_quality(
        _mkt(best_bid=100_020.0, best_ask=100_010.0), MODERADO)
    assert any("book" in v for v in viol)


def test_limite_diario_de_ordens():
    pf = PortfolioState(equity=1000.0, cash=1000.0, orders_today=4)
    viol = check_frequency("BTCUSDT", pf, MODERADO, NOW)
    assert any("ordens no dia" in v for v in viol)


def test_cooldown_por_ativo():
    pf = PortfolioState(equity=1000.0, cash=1000.0, orders_today=1,
                        last_order_at={"BTCUSDT": NOW - timedelta(hours=1)})
    viol = check_frequency("BTCUSDT", pf, MODERADO, NOW)
    assert any("cooldown" in v for v in viol)
    # outro ativo não está em cooldown
    assert check_frequency("ETHUSDT", pf, MODERADO, NOW) == []
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `python3 -m pytest tests/test_gates.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar**

`src/invest_agent/gates.py`:

```python
"""Gates pré-ordem. Cada função retorna a lista de violações (em
português — os textos vão direto ao Telegram). Lista vazia = passou.
Nunca opere com dado velho; nunca negocie contra um book quebrado."""
from __future__ import annotations

from datetime import datetime, timedelta

from invest_agent.config import RiskProfile
from invest_agent.models import MarketSnapshot, PortfolioState


def check_market_quality(market: MarketSnapshot, profile: RiskProfile) -> list[str]:
    viol: list[str] = []
    if market.candle_age_seconds > profile.max_candle_age_seconds:
        viol.append(
            f"dado de mercado velho ({market.candle_age_seconds:.0f}s > "
            f"{profile.max_candle_age_seconds:.0f}s)")
    if not (0 < market.best_bid <= market.best_ask):
        viol.append("book inconsistente (bid/ask inválidos)")
    else:
        mid = (market.best_bid + market.best_ask) / 2
        spread = (market.best_ask - market.best_bid) / mid
        if spread > profile.max_spread_pct:
            viol.append(
                f"spread {spread:.2%} acima do máximo {profile.max_spread_pct:.2%}")
    if market.quote_volume_24h <= 0:
        viol.append("volume 24h zerado")
    return viol


def check_frequency(
    symbol: str,
    portfolio: PortfolioState,
    profile: RiskProfile,
    now: datetime,
) -> list[str]:
    viol: list[str] = []
    if portfolio.orders_today >= profile.max_orders_per_day:
        viol.append(
            f"máximo de ordens no dia atingido ({profile.max_orders_per_day})")
    last = portfolio.last_order_at.get(symbol)
    if last is not None:
        elapsed = now - last
        cooldown = timedelta(hours=profile.cooldown_hours)
        if elapsed < cooldown:
            restante = cooldown - elapsed
            viol.append(
                f"cooldown de {symbol} ativo (faltam {restante})")
    return viol
```

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python3 -m pytest tests/test_gates.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/gates.py tests/test_gates.py
git commit -m "feat: gates de qualidade de mercado e frequência anti-overtrading"
```

---

### Task 6: Circuit breakers de drawdown

**Files:**
- Create: `src/invest_agent/breakers.py`
- Test: `tests/test_breakers.py`

**Interfaces:**
- Consumes: `RiskProfile` (Task 2)
- Produces:
  - `HaltLevel` (Enum): `NONE | DAY | WEEK | MONTH` (MONTH é o mais grave; retomada só manual)
  - `EquityMarks(day_open: float, week_open: float, month_open: float)` (frozen dataclass) — snapshots de equity no início do dia/semana/mês, persistidos pelo orquestrador (Fase 2)
  - `check_breakers(equity_now: float, marks: EquityMarks, profile: RiskProfile) -> HaltLevel` — retorna o nível MAIS GRAVE violado

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_breakers.py`:

```python
from invest_agent.breakers import EquityMarks, HaltLevel, check_breakers
from invest_agent.config import MODERADO

MARKS = EquityMarks(day_open=1000.0, week_open=1000.0, month_open=1000.0)


def test_sem_perda_sem_halt():
    assert check_breakers(1000.0, MARKS, MODERADO) is HaltLevel.NONE


def test_perda_diaria_de_5pct_halta_o_dia():
    assert check_breakers(950.0, MARKS, MODERADO) is HaltLevel.DAY


def test_perda_semanal_de_10pct_halta_a_semana():
    marks = EquityMarks(day_open=905.0, week_open=1000.0, month_open=1000.0)
    assert check_breakers(900.0, marks, MODERADO) is HaltLevel.WEEK


def test_retorna_o_nivel_mais_grave():
    # -15% no mês E -5% no dia => MONTH vence
    assert check_breakers(850.0, MARKS, MODERADO) is HaltLevel.MONTH


def test_lucro_nunca_halta():
    assert check_breakers(1200.0, MARKS, MODERADO) is HaltLevel.NONE
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `python3 -m pytest tests/test_breakers.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar**

`src/invest_agent/breakers.py`:

```python
"""Circuit breakers de drawdown. O orquestrador (Fase 2) persiste os
marks e converte o HaltLevel em ação: DAY = pausa até D+1, WEEK = pausa
+ alerta, MONTH = desliga e exige retomada manual pelo dono."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from invest_agent.config import RiskProfile


class HaltLevel(Enum):
    NONE = 0
    DAY = 1
    WEEK = 2
    MONTH = 3


@dataclass(frozen=True)
class EquityMarks:
    day_open: float
    week_open: float
    month_open: float


def _drawdown(open_value: float, now_value: float) -> float:
    if open_value <= 0:
        return 0.0
    return max(0.0, (open_value - now_value) / open_value)


def check_breakers(
    equity_now: float,
    marks: EquityMarks,
    profile: RiskProfile,
) -> HaltLevel:
    if _drawdown(marks.month_open, equity_now) >= profile.monthly_loss_halt_pct:
        return HaltLevel.MONTH
    if _drawdown(marks.week_open, equity_now) >= profile.weekly_loss_halt_pct:
        return HaltLevel.WEEK
    if _drawdown(marks.day_open, equity_now) >= profile.daily_loss_halt_pct:
        return HaltLevel.DAY
    return HaltLevel.NONE
```

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python3 -m pytest tests/test_breakers.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/breakers.py tests/test_breakers.py
git commit -m "feat: circuit breakers de drawdown (dia/semana/mês)"
```

---

### Task 7: Kill switch e dead-man switch

**Files:**
- Create: `src/invest_agent/killswitch.py`
- Test: `tests/test_killswitch.py`

**Interfaces:**
- Consumes: nada dos módulos anteriores
- Produces:
  - `KillSwitch(path: Path)` com `activate(reason: str) -> None`, `deactivate() -> None`, `is_active() -> bool`, `reason() -> str | None`. A flag é um ARQUIVO em disco de propósito: sobrevive à morte do processo, e o `/kill` do Telegram (Fase 2) só precisa criar o arquivo.
  - `heartbeat_beat(path: Path, now: datetime) -> None` e `heartbeat_stale(path: Path, max_age_seconds: float, now: datetime) -> bool` — heartbeat como timestamp ISO em arquivo; `stale=True` também quando o arquivo não existe (falha fechada).

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_killswitch.py`:

```python
from datetime import datetime, timedelta, timezone

from invest_agent.killswitch import KillSwitch, heartbeat_beat, heartbeat_stale

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def test_killswitch_ativa_desativa(tmp_path):
    ks = KillSwitch(tmp_path / "KILL")
    assert not ks.is_active()
    ks.activate("comando /kill do dono")
    assert ks.is_active()
    assert ks.reason() == "comando /kill do dono"
    ks.deactivate()
    assert not ks.is_active()


def test_heartbeat_fresco_nao_esta_stale(tmp_path):
    hb = tmp_path / "heartbeat"
    heartbeat_beat(hb, NOW)
    assert not heartbeat_stale(hb, max_age_seconds=300, now=NOW + timedelta(seconds=60))


def test_heartbeat_velho_esta_stale(tmp_path):
    hb = tmp_path / "heartbeat"
    heartbeat_beat(hb, NOW)
    assert heartbeat_stale(hb, max_age_seconds=300, now=NOW + timedelta(seconds=301))


def test_heartbeat_inexistente_falha_fechado(tmp_path):
    assert heartbeat_stale(tmp_path / "nao-existe", max_age_seconds=300, now=NOW)
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `python3 -m pytest tests/test_killswitch.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar**

`src/invest_agent/killswitch.py`:

```python
"""Kill switch = arquivo em disco. Matar o processo não apaga a flag;
o supervisor externo e o bot do Telegram só precisam de filesystem.
Heartbeat falha FECHADO: sem arquivo = stale = halt."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


class KillSwitch:
    def __init__(self, path: Path) -> None:
        self.path = path

    def activate(self, reason: str) -> None:
        self.path.write_text(reason, encoding="utf-8")

    def deactivate(self) -> None:
        self.path.unlink(missing_ok=True)

    def is_active(self) -> bool:
        return self.path.exists()

    def reason(self) -> str | None:
        if not self.is_active():
            return None
        return self.path.read_text(encoding="utf-8")


def heartbeat_beat(path: Path, now: datetime) -> None:
    path.write_text(now.isoformat(), encoding="utf-8")


def heartbeat_stale(path: Path, max_age_seconds: float, now: datetime) -> bool:
    if not path.exists():
        return True
    try:
        last = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return True
    return (now - last).total_seconds() > max_age_seconds
```

- [ ] **Step 4: Rodar e confirmar que passam**

Run: `python3 -m pytest tests/test_killswitch.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/killswitch.py tests/test_killswitch.py
git commit -m "feat: kill switch em arquivo + dead-man switch (falha fechado)"
```

---

### Task 8: RulesEngine — integração de tudo

**Files:**
- Create: `src/invest_agent/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: TUDO das tasks 1–7: `Proposal/Action/PortfolioState/MarketSnapshot/OrderIntent/Verdict/VerdictStatus` (T1), `RiskProfile/MODERADO` (T2), `build_whitelist` só indiretamente — o engine recebe a whitelist pronta como `frozenset[str]` (T3), `compute_buy_qty` (T4), `check_market_quality/check_frequency` (T5), `HaltLevel/EquityMarks/check_breakers` (T6), `KillSwitch` (T7)
- Produces: `RulesEngine(profile: RiskProfile, whitelist: frozenset[str], kill_switch: KillSwitch)` com o método único:
  `evaluate(proposal: Proposal, portfolio: PortfolioState, market: MarketSnapshot, marks: EquityMarks, now: datetime) -> Verdict`
  Comportamento:
  - `HOLD` → APPROVED com `order=None` (não fazer nada é sempre permitido)
  - kill switch ativo, breaker ≠ NONE, símbolo fora da whitelist, gates violados → REJECTED com todos os motivos acumulados
  - `BUY`: sizing via `compute_buy_qty` (qty 0 → REJECTED "sem espaço"); `limit_price = best_ask`; `stop_loss_price = limit * (1 - stop_loss_pct)` — stop SEMPRE presente em compra
  - `SELL`/`CLOSE`: exige posição existente (só operamos long); vende a posição inteira; `limit_price = best_bid`; sem stop
  - notional > `hitl_threshold_pct * equity` → NEEDS_APPROVAL (com a ordem anexada, para o Telegram mostrar)
  - `client_order_id` determinístico: `"ia-" + sha1(f"{cycle_id}:{symbol}:{side}").hexdigest()[:17]` — reprocessar o mesmo ciclo NUNCA gera ordem duplicada

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_engine.py`:

```python
from datetime import datetime, timezone

import pytest

from invest_agent.breakers import EquityMarks
from invest_agent.config import MODERADO
from invest_agent.engine import RulesEngine
from invest_agent.killswitch import KillSwitch
from invest_agent.models import (
    Action, MarketSnapshot, PortfolioState, Position, Proposal, VerdictStatus,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
MARKS = EquityMarks(day_open=1000.0, week_open=1000.0, month_open=1000.0)
WL = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})


@pytest.fixture
def engine(tmp_path):
    return RulesEngine(profile=MODERADO, whitelist=WL,
                       kill_switch=KillSwitch(tmp_path / "KILL"))


def _mkt(symbol="SOLUSDT", price=50.0):
    return MarketSnapshot(symbol=symbol, last_price=price,
                          best_bid=price * 0.999, best_ask=price * 1.001,
                          candle_age_seconds=60.0, quote_volume_24h=1e8)


def _pf(**kw):
    base = dict(equity=10_000.0, cash=10_000.0, positions={},
                orders_today=0, last_order_at={})
    base.update(kw)
    return PortfolioState(**base)


def _prop(symbol="SOLUSDT", action=Action.BUY, conviction=0.10):
    return Proposal(symbol=symbol, action=action, conviction=conviction,
                    rationale="teste", cycle_id="ciclo-1")


def test_hold_aprovado_sem_ordem(engine):
    v = engine.evaluate(_prop(action=Action.HOLD), _pf(), _mkt(), MARKS, NOW)
    assert v.status is VerdictStatus.APPROVED and v.order is None


def test_compra_aprovada_tem_stop_loss_e_id_deterministico(engine):
    # conviction 0.10 => 10000*0.10*0.10 = 100 USDT = 1% < HITL 2%
    v = engine.evaluate(_prop(), _pf(), _mkt(), MARKS, NOW)
    assert v.status is VerdictStatus.APPROVED
    assert v.order is not None
    assert v.order.stop_loss_price == pytest.approx(
        v.order.limit_price * (1 - MODERADO.stop_loss_pct))
    v2 = engine.evaluate(_prop(), _pf(), _mkt(), MARKS, NOW)
    assert v.order.client_order_id == v2.order.client_order_id
    assert v.order.client_order_id.startswith("ia-")


def test_fora_da_whitelist_rejeita(engine):
    v = engine.evaluate(_prop(symbol="SCAMUSDT"), _pf(),
                        _mkt(symbol="SCAMUSDT"), MARKS, NOW)
    assert v.status is VerdictStatus.REJECTED
    assert any("whitelist" in r for r in v.reasons)


def test_kill_switch_bloqueia_tudo(engine):
    engine.kill_switch.activate("teste")
    v = engine.evaluate(_prop(), _pf(), _mkt(), MARKS, NOW)
    assert v.status is VerdictStatus.REJECTED
    assert any("kill switch" in r for r in v.reasons)


def test_breaker_bloqueia(engine):
    marks = EquityMarks(day_open=11_000.0, week_open=11_000.0,
                        month_open=11_000.0)  # equity 10k = -9% no dia
    v = engine.evaluate(_prop(), _pf(), _mkt(), marks, NOW)
    assert v.status is VerdictStatus.REJECTED
    assert any("circuit breaker" in r for r in v.reasons)


def test_ordem_grande_pede_aprovacao_humana(engine):
    # conviction 1.0 => alvo 1000 USDT = 10% > HITL 2%
    v = engine.evaluate(_prop(conviction=1.0), _pf(), _mkt(), MARKS, NOW)
    assert v.status is VerdictStatus.NEEDS_APPROVAL
    assert v.order is not None


def test_venda_sem_posicao_rejeita(engine):
    v = engine.evaluate(_prop(action=Action.SELL), _pf(), _mkt(), MARKS, NOW)
    assert v.status is VerdictStatus.REJECTED
    assert any("sem posição" in r for r in v.reasons)


def test_close_vende_a_posicao_inteira(engine):
    pf = _pf(positions={"SOLUSDT": Position("SOLUSDT", qty=3.0, avg_price=40.0)})
    v = engine.evaluate(_prop(action=Action.CLOSE, conviction=1.0),
                        pf, _mkt(), MARKS, NOW)
    # 3 * ~50 = ~150 USDT = 1.5% < HITL => aprovado direto
    assert v.status is VerdictStatus.APPROVED
    assert v.order.side == "SELL" and v.order.qty == 3.0
    assert v.order.stop_loss_price is None
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar**

`src/invest_agent/engine.py`:

```python
"""O gate central: a ÚNICA porta entre uma proposta do LLM e uma ordem.
Toda regra é acumulativa — o Verdict rejeitado carrega TODOS os motivos,
para o dono ver o quadro completo no Telegram."""
from __future__ import annotations

import hashlib
from datetime import datetime

from invest_agent.breakers import EquityMarks, HaltLevel, check_breakers
from invest_agent.config import RiskProfile
from invest_agent.gates import check_frequency, check_market_quality
from invest_agent.killswitch import KillSwitch
from invest_agent.models import (
    Action, MarketSnapshot, OrderIntent, PortfolioState, Proposal,
    Verdict, VerdictStatus,
)
from invest_agent.sizing import compute_buy_qty


def _client_order_id(cycle_id: str, symbol: str, side: str) -> str:
    digest = hashlib.sha1(f"{cycle_id}:{symbol}:{side}".encode()).hexdigest()
    return f"ia-{digest[:17]}"


class RulesEngine:
    def __init__(
        self,
        profile: RiskProfile,
        whitelist: frozenset[str],
        kill_switch: KillSwitch,
    ) -> None:
        self.profile = profile
        self.whitelist = whitelist
        self.kill_switch = kill_switch

    def evaluate(
        self,
        proposal: Proposal,
        portfolio: PortfolioState,
        market: MarketSnapshot,
        marks: EquityMarks,
        now: datetime,
    ) -> Verdict:
        if proposal.action is Action.HOLD:
            return Verdict(VerdictStatus.APPROVED, [], None)

        reasons: list[str] = []

        if self.kill_switch.is_active():
            reasons.append(f"kill switch ativo: {self.kill_switch.reason()}")

        halt = check_breakers(portfolio.equity, marks, self.profile)
        if halt is not HaltLevel.NONE:
            reasons.append(f"circuit breaker acionado (nível {halt.name})")

        if proposal.symbol not in self.whitelist:
            reasons.append(f"{proposal.symbol} fora da whitelist")

        reasons += check_market_quality(market, self.profile)
        reasons += check_frequency(proposal.symbol, portfolio, self.profile, now)

        if reasons:
            return Verdict(VerdictStatus.REJECTED, reasons, None)

        if proposal.action is Action.BUY:
            order = self._build_buy(proposal, portfolio, market)
            if order is None:
                return Verdict(
                    VerdictStatus.REJECTED,
                    ["sem espaço nos tetos de posição/exposição para comprar"],
                    None)
        else:  # SELL ou CLOSE: só fechamos posição existente (long-only)
            position = portfolio.positions.get(proposal.symbol)
            if position is None or position.qty <= 0:
                return Verdict(
                    VerdictStatus.REJECTED,
                    [f"sem posição em {proposal.symbol} para vender"],
                    None)
            order = OrderIntent(
                symbol=proposal.symbol,
                side="SELL",
                qty=position.qty,
                limit_price=market.best_bid,
                stop_loss_price=None,
                client_order_id=_client_order_id(
                    proposal.cycle_id, proposal.symbol, "SELL"),
            )

        notional = order.qty * order.limit_price
        if notional > self.profile.hitl_threshold_pct * portfolio.equity:
            return Verdict(
                VerdictStatus.NEEDS_APPROVAL,
                [f"ordem de {notional:.2f} USDT acima do limiar de "
                 f"aprovação humana "
                 f"({self.profile.hitl_threshold_pct:.0%} do capital)"],
                order)

        return Verdict(VerdictStatus.APPROVED, [], order)

    def _build_buy(
        self,
        proposal: Proposal,
        portfolio: PortfolioState,
        market: MarketSnapshot,
    ) -> OrderIntent | None:
        position = portfolio.positions.get(proposal.symbol)
        current_notional = (
            position.notional(market.last_price) if position else 0.0)
        total_invested = sum(
            p.notional(market.last_price) if p.symbol == market.symbol
            else p.notional(p.avg_price)
            for p in portfolio.positions.values())
        qty = compute_buy_qty(
            equity=portfolio.equity,
            price=market.best_ask,
            conviction=proposal.conviction,
            current_position_notional=current_notional,
            total_invested_notional=total_invested,
            profile=self.profile)
        if qty <= 0:
            return None
        limit = market.best_ask
        return OrderIntent(
            symbol=proposal.symbol,
            side="BUY",
            qty=qty,
            limit_price=limit,
            stop_loss_price=limit * (1 - self.profile.stop_loss_pct),
            client_order_id=_client_order_id(
                proposal.cycle_id, proposal.symbol, "BUY"),
        )
```

- [ ] **Step 4: Rodar TODA a suíte e confirmar que passa**

Run: `python3 -m pytest -v`
Expected: todos os testes das tasks 1–8 passed (28 no total)

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/engine.py tests/test_engine.py
git commit -m "feat: RulesEngine integrando gates, breakers, sizing, HITL e kill switch"
```

---

### Task 9: README da Fase 0

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: tudo (documenta o que existe)
- Produces: documentação de entrada do repo

- [ ] **Step 1: Escrever o README**

`README.md`:

```markdown
# invest-agent

Agente de investimento pessoal: **o LLM propõe, código dispõe.**

Modelos Claude analisam mercado e notícias e produzem *propostas*
(`Proposal`); um motor de regras determinístico (`RulesEngine`) valida
cada proposta contra whitelist, sizing, exposição, frequência, circuit
breakers e kill switch antes de qualquer ordem existir. O LLM nunca vê
chave de API e nunca calcula tamanho de posição.

- Spec: `docs/superpowers/specs/2026-09-05-invest-agent-design.md`
- Pesquisa que fundamenta o design: `~/Documents/research/` (6 relatórios)

## Estado atual

**Fase 0 concluída:** modelos de domínio + motor de regras completo,
100% testado, zero rede/LLM. Próximas fases (spec §5): ingestão +
backtest → paper trading (testnet Binance + Telegram + loop Claude) →
live micro com R$ 1.000.

## Rodar os testes

    python3 -m pip install pytest
    python3 -m pytest -v

## Perfil de risco ativo: moderado

Máx 10% por ativo · máx 60% investido · stop-loss 5% em toda compra ·
≤4 ordens/dia · cooldown 4h por ativo · halt a −5% dia / −10% semana /
−15% mês · aprovação humana via Telegram acima de 2% do capital.
Valores em `src/invest_agent/config.py` — só um humano edita.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README da fase 0"
```

---

## Self-review (executado na escrita do plano)

- **Cobertura da spec §4.4:** whitelist dinâmica (T3), sizing por código (T4), sanidade/liquidez/freshness + anti-overtrading (T5), circuit breakers −5/−10/−15 (T6), kill switch + dead-man (T7), stop-loss obrigatório + HITL 2% + idempotência (T8). Itens da §4.4 que ficam para a Fase 2 por exigirem I/O real: teto de custo de API diário, taxa de erro de tools, cotação de ordem pós-circuit-breaker exigir HITL (regra de estado do orquestrador), níveis 2/3 do kill switch (flags por estratégia/instância — YAGNI com uma só estratégia).
- **Placeholders:** nenhum; todo step tem código completo.
- **Consistência de tipos:** assinaturas de T4/T5/T6/T7 conferidas contra os usos em T8.
