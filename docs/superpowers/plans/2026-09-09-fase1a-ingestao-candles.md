# Fase 1a — Ingestão de Candles + Armazenamento + Indicadores — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pipeline de candles da Binance (REST + histórico data.binance.vision) persistido em Parquet/DuckDB, indicadores em Python puro, e coleta real de `SymbolStats` para a whitelist da Fase 0 — a fundação de dados do backtest (Fase 1b).

**Architecture:** Módulos novos sob `src/invest_agent/data/` (fetchers com transporte HTTP injetável — nenhum teste toca a rede) + `src/invest_agent/indicators.py` (funções puras). Armazenamento local: Parquet particionado por símbolo/intervalo/mês, lido via DuckDB. Um CLI (`python3 -m invest_agent.data.ingest`) é a única borda que lê o relógio e a rede reais.

**Tech Stack:** Python ≥ 3.12; runtime: stdlib + `duckdb` + `pyarrow` (primeiras dependências de runtime do projeto — permitidas a partir da Fase 1); dev: pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-invest-agent-design.md` (§4.1 ingestão de candles, §4.2 Parquet+DuckDB, §4.3 "indicadores calculados em Python", §4.4 whitelist dinâmica). Escopo 1a = só candles/indicadores/símbolos; notícias/RSS/macro ficam para o plano 1c; backtest para o 1b.

## Global Constraints

- Python ≥ 3.12; runtime deps são SOMENTE `duckdb>=1.1` e `pyarrow>=17`; `pytest` só em dev.
- **Nenhum teste acessa a rede.** Todo I/O HTTP passa por um parâmetro `transport: Callable[[str], bytes]` injetável; testes usam fakes.
- Nenhum módulo lê o relógio dentro da lógica (`now` sempre parâmetro). Exceção única e documentada: `main()` do CLI (borda de composição).
- Todas as datas/horas timezone-aware UTC.
- Mensagens de erro e saída de CLI em português.
- Módulos de `data/` podem importar `invest_agent.whitelist.SymbolStats`; o motor de regras (Fase 0) NUNCA importa `data/`.
- Timestamps da Binance: REST usa milissegundos; arquivos do data.binance.vision de 2025+ usam **microssegundos** — a normalização vive em um único lugar (`Candle.from_kline_row`).
- Commits pequenos e frequentes; mensagens concisas, sem assinatura.

---

### Task 1: Dependências + modelo `Candle`

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `src/invest_agent/data/__init__.py`
- Create: `src/invest_agent/data/models.py`
- Test: `tests/test_data_models.py`

**Interfaces:**
- Consumes: nada da Fase 0.
- Produces: `Candle` (dataclass frozen, campos: `symbol: str`, `interval: str`, `open_time: datetime`, `open: float`, `high: float`, `low: float`, `close: float`, `volume: float`, `quote_volume: float`, `n_trades: int`, `close_time: datetime`) e `Candle.from_kline_row(symbol: str, interval: str, row: list) -> Candle`. Todas as tasks seguintes dependem destes nomes exatos.

- [ ] **Step 1: Instalar dependências novas**

```bash
python3 -m pip install "duckdb>=1.1" "pyarrow>=17"
```

Em `pyproject.toml`, trocar a linha `dependencies = []` por:

```toml
dependencies = ["duckdb>=1.1", "pyarrow>=17"]
```

Em `.gitignore`, acrescentar a linha:

```
data/
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_data_models.py
from datetime import datetime, timezone

from invest_agent.data.models import Candle

# Linha real de kline REST (ms): open_time, open, high, low, close, volume,
# close_time, quote_volume, n_trades, taker_base, taker_quote, ignore
ROW_MS = [1704067200000, "42283.58", "42554.57", "42261.02", "42475.23",
          "1271.68", 1704070799999, "53950211.04", 47134, "600.0", "25000000.0", "0"]

# Mesma linha como viria de um CSV 2025+ do data.binance.vision (µs)
ROW_US = [1704067200000000, "42283.58", "42554.57", "42261.02", "42475.23",
          "1271.68", 1704070799999999, "53950211.04", 47134, "600.0", "25000000.0", "0"]


def test_from_kline_row_ms():
    c = Candle.from_kline_row("BTCUSDT", "1h", ROW_MS)
    assert c.symbol == "BTCUSDT" and c.interval == "1h"
    assert c.open_time == datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert c.open == 42283.58 and c.close == 42475.23
    assert c.quote_volume == 53950211.04 and c.n_trades == 47134
    assert c.close_time == datetime(2024, 1, 1, 0, 59, 59, 999000, tzinfo=timezone.utc)


def test_from_kline_row_microssegundos_normaliza():
    ms = Candle.from_kline_row("BTCUSDT", "1h", ROW_MS)
    us = Candle.from_kline_row("BTCUSDT", "1h", ROW_US)
    assert us.open_time == ms.open_time


def test_candle_e_imutavel():
    c = Candle.from_kline_row("BTCUSDT", "1h", ROW_MS)
    try:
        c.close = 0.0
        assert False, "devia ser frozen"
    except AttributeError:
        pass
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_data_models.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'invest_agent.data'`

- [ ] **Step 4: Write minimal implementation**

`src/invest_agent/data/__init__.py`: arquivo vazio.

```python
# src/invest_agent/data/models.py
"""Modelo de candle e a ÚNICA normalização de timestamps da Binance:
REST usa milissegundos; arquivos do data.binance.vision de 2025+ usam
microssegundos. Detectamos pelo valor (>= 10^14 => µs)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

_MICROS_THRESHOLD = 100_000_000_000_000  # 10^14


def _to_utc(ts: int | float | str) -> datetime:
    ts = int(ts)
    if ts >= _MICROS_THRESHOLD:
        ts //= 1000  # µs -> ms
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    interval: str
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float          # volume na moeda base
    quote_volume: float    # volume na moeda de cotação (USDT)
    n_trades: int
    close_time: datetime

    @classmethod
    def from_kline_row(cls, symbol: str, interval: str, row: list) -> "Candle":
        """Linha de kline (REST /api/v3/klines ou CSV do data.binance.vision).
        As 12 colunas têm a mesma ordem nos dois formatos; usamos as 9 primeiras."""
        return cls(
            symbol=symbol,
            interval=interval,
            open_time=_to_utc(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            close_time=_to_utc(row[6]),
            quote_volume=float(row[7]),
            n_trades=int(row[8]),
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_data_models.py -v`
Expected: PASS (3 testes). Rodar também a suíte completa: `python3 -m pytest` — 38 pré-existentes + 3 novos.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/invest_agent/data/ tests/test_data_models.py
git commit -m "feat: modelo Candle com normalização ms/µs + deps duckdb/pyarrow"
```

---

### Task 2: Indicadores em Python puro

**Files:**
- Create: `src/invest_agent/indicators.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Consumes: nada.
- Produces: `sma(values: list[float], period: int) -> list[float | None]`, `ema(values: list[float], period: int) -> list[float | None]`, `rsi(values: list[float], period: int = 14) -> list[float | None]`, `atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]`. Saída sempre do MESMO comprimento da entrada, `None` onde não há valor definido. O backtest (plano 1b) consome estas assinaturas.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_indicators.py
import pytest

from invest_agent.indicators import atr, ema, rsi, sma


def test_sma_janela_2():
    assert sma([1.0, 2.0, 3.0, 4.0], 2) == [None, 1.5, 2.5, 3.5]


def test_sma_periodo_invalido():
    with pytest.raises(ValueError):
        sma([1.0], 0)


def test_ema_seed_e_suavizacao():
    # period=3: seed = SMA(1,2,3) = 2.0 no índice 2; k = 0.5
    # idx3 = 4*0.5 + 2*0.5 = 3.0 ; idx4 = 5*0.5 + 3*0.5 = 4.0
    assert ema([1.0, 2.0, 3.0, 4.0, 5.0], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_serie_curta_e_toda_none():
    assert ema([1.0, 2.0], 3) == [None, None]


def test_rsi_alta_continua_e_100():
    valores = [float(i) for i in range(1, 20)]
    out = rsi(valores, 14)
    assert out[14] == 100.0 and out[-1] == 100.0


def test_rsi_queda_continua_e_0():
    valores = [float(i) for i in range(20, 1, -1)]
    out = rsi(valores, 14)
    assert out[14] == 0.0 and out[-1] == 0.0


def test_rsi_ganhos_e_perdas_iguais_da_50():
    out = rsi([1.0, 2.0, 1.0], 2)
    assert out == [None, None, 50.0]


def test_atr_wilder_calculado_a_mao():
    # TR: [2, max(3,3,0)=3, max(1,0,1)=1]; period=2:
    # atr[1] = (2+3)/2 = 2.5 ; atr[2] = (2.5*1 + 1)/2 = 1.75
    highs, lows, closes = [10.0, 12.0, 11.0], [8.0, 9.0, 10.0], [9.0, 11.0, 10.5]
    assert atr(highs, lows, closes, 2) == [None, 2.5, 1.75]


def test_atr_series_de_tamanhos_diferentes():
    with pytest.raises(ValueError):
        atr([1.0], [1.0, 2.0], [1.0], 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_indicators.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'invest_agent.indicators'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/indicators.py
"""Indicadores em Python puro (spec §4.3: "candles+indicadores calculados
em Python"). Entradas em ordem cronológica; a saída tem o MESMO comprimento
da entrada, com None nas posições sem valor definido. RSI e ATR usam a
suavização de Wilder."""
from __future__ import annotations


def _check_period(period: int) -> None:
    if period <= 0:
        raise ValueError("period deve ser positivo")


def sma(values: list[float], period: int) -> list[float | None]:
    _check_period(period)
    out: list[float | None] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= period:
            acc -= values[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    _check_period(period)
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period  # seed = SMA do primeiro período
    out[period - 1] = prev
    k = 2 / (period + 1)
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    _check_period(period)
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain, avg_loss = gains / period, losses / period

    def _rsi(g: float, p: float) -> float:
        if p == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / p)

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def atr(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> list[float | None]:
    _check_period(period)
    n = len(closes)
    if not (len(highs) == len(lows) == n):
        raise ValueError("séries de tamanhos diferentes")
    out: list[float | None] = [None] * n
    if n == 0:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    if n < period:
        return out
    prev = sum(trs[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_indicators.py -v`
Expected: PASS (9 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/indicators.py tests/test_indicators.py
git commit -m "feat: indicadores puros (SMA, EMA, RSI e ATR de Wilder)"
```

---

### Task 3: Cliente REST público da Binance

**Files:**
- Create: `src/invest_agent/data/binance_client.py`
- Test: `tests/test_binance_client.py`

**Interfaces:**
- Consumes: `Candle.from_kline_row` (Task 1).
- Produces: classe `BinanceMarketData(base_url: str = BASE_URL, transport: Callable[[str], bytes] | None = None, sleeper: Callable[[float], None] = time.sleep)` com métodos `klines(symbol, interval, start: datetime | None = None, end: datetime | None = None, limit: int = 1000) -> list[Candle]`, `klines_range(symbol, interval, start: datetime, end: datetime, limit: int = 1000) -> list[Candle]` (paginação automática) e `exchange_info() -> dict`. Exceções: `BinanceError`, `IPBannedError`. Tasks 6 e 7 consomem exatamente estes nomes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_binance_client.py
import json
import urllib.error
from datetime import datetime, timezone

import pytest

from invest_agent.data.binance_client import (
    BinanceError, BinanceMarketData, IPBannedError,
)

ROW = [1704067200000, "1", "2", "0.5", "1.5", "10", 1704070799999,
       "15", 3, "5", "7.5", "0"]


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", None, None)


def test_klines_monta_url_e_converte():
    urls = []

    def transport(url: str) -> bytes:
        urls.append(url)
        return json.dumps([ROW]).encode()

    client = BinanceMarketData(transport=transport)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = client.klines("BTCUSDT", "1h", start=start, limit=500)
    assert len(out) == 1 and out[0].close == 1.5
    assert "symbol=BTCUSDT" in urls[0] and "interval=1h" in urls[0]
    assert "startTime=1704067200000" in urls[0] and "limit=500" in urls[0]


def test_retry_em_429_respeita_retry_after():
    calls, sleeps = [], []

    def transport(url: str) -> bytes:
        calls.append(url)
        if len(calls) == 1:
            err = _http_error(429)
            err.headers = {"Retry-After": "3"}
            raise err
        return b"[]"

    client = BinanceMarketData(transport=transport, sleeper=sleeps.append)
    assert client.klines("BTCUSDT", "1h") == []
    assert len(calls) == 2 and sleeps == [3.0]


def test_418_e_ban_sem_retry():
    def transport(url: str) -> bytes:
        raise _http_error(418)

    client = BinanceMarketData(transport=transport, sleeper=lambda s: None)
    with pytest.raises(IPBannedError):
        client.klines("BTCUSDT", "1h")


def test_5xx_persistente_vira_binance_error():
    def transport(url: str) -> bytes:
        raise _http_error(500)

    client = BinanceMarketData(transport=transport, sleeper=lambda s: None)
    with pytest.raises(BinanceError):
        client.klines("BTCUSDT", "1h")


def test_klines_range_pagina_ate_o_fim():
    hora = 3_600_000
    t0 = 1704067200000

    def row(open_ms: int) -> list:
        return [open_ms, "1", "2", "0.5", "1.5", "10", open_ms + hora - 1,
                "15", 3, "5", "7.5", "0"]

    pages = [
        [row(t0), row(t0 + hora)],          # página cheia (limit=2)
        [row(t0 + 2 * hora)],               # página final
    ]

    def transport(url: str) -> bytes:
        return json.dumps(pages.pop(0)).encode()

    client = BinanceMarketData(transport=transport)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, tzinfo=timezone.utc)
    out = client.klines_range("BTCUSDT", "1h", start, end, limit=2)
    assert len(out) == 3
    assert [c.open_time.hour for c in out] == [0, 1, 2]


def test_datetime_naive_e_rejeitado():
    client = BinanceMarketData(transport=lambda u: b"[]")
    with pytest.raises(ValueError):
        client.klines("BTCUSDT", "1h", start=datetime(2024, 1, 1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_binance_client.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/data/binance_client.py
"""Cliente REST público da Binance — SOMENTE dados de mercado, sem
credenciais (a execução com chaves é da Fase 2, spec §4.5). Todo I/O passa
por `transport`, injetável; nenhum teste acessa a rede."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable

from .models import Candle

BASE_URL = "https://api.binance.com"
MAX_LIMIT = 1000
_RETRIES = 5


class BinanceError(Exception):
    """Falha de comunicação com a Binance."""


class IPBannedError(BinanceError):
    """HTTP 418: IP banido — nunca fazer retry (spec §4.5)."""


def _default_transport(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def _ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("datetime deve ser timezone-aware (UTC)")
    return int(dt.timestamp() * 1000)


class BinanceMarketData:
    def __init__(self, base_url: str = BASE_URL,
                 transport: Callable[[str], bytes] | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self._base = base_url.rstrip("/")
        self._transport = transport or _default_transport
        self._sleep = sleeper

    def _get(self, path: str, params: dict) -> object:
        query = urllib.parse.urlencode(params)
        url = f"{self._base}{path}" + (f"?{query}" if query else "")
        delay = 1.0
        for attempt in range(_RETRIES):
            try:
                return json.loads(self._transport(url))
            except urllib.error.HTTPError as err:
                if err.code == 418:
                    raise IPBannedError(
                        f"IP banido pela Binance (418) em {path}") from err
                if err.code == 429 or err.code >= 500:
                    if attempt == _RETRIES - 1:
                        raise BinanceError(
                            f"HTTP {err.code} em {path} após {_RETRIES} tentativas") from err
                    retry_after = None
                    if getattr(err, "headers", None):
                        retry_after = err.headers.get("Retry-After")
                    self._sleep(float(retry_after) if retry_after else delay)
                    delay *= 2
                    continue
                raise BinanceError(f"HTTP {err.code} em {path}") from err
        raise AssertionError("inalcançável")

    def klines(self, symbol: str, interval: str,
               start: datetime | None = None, end: datetime | None = None,
               limit: int = MAX_LIMIT) -> list[Candle]:
        params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
        if start is not None:
            params["startTime"] = _ms(start)
        if end is not None:
            params["endTime"] = _ms(end)
        rows = self._get("/api/v3/klines", params)
        return [Candle.from_kline_row(symbol, interval, r) for r in rows]

    def klines_range(self, symbol: str, interval: str,
                     start: datetime, end: datetime,
                     limit: int = MAX_LIMIT) -> list[Candle]:
        """Pagina até cobrir [start, end). O cursor avança para o close_time
        do último candle: o próximo candle abre 1 ms depois dele."""
        out: list[Candle] = []
        cursor = start
        while True:
            batch = self.klines(symbol, interval, start=cursor, end=end, limit=limit)
            if not batch:
                break
            out.extend(batch)
            if len(batch) < limit:
                break
            cursor = batch[-1].close_time
        return out

    def exchange_info(self) -> dict:
        return self._get("/api/v3/exchangeInfo", {})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_binance_client.py -v`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/data/binance_client.py tests/test_binance_client.py
git commit -m "feat: cliente REST público da Binance com retry/backoff e paginação"
```

---

### Task 4: `CandleStore` — Parquet particionado + DuckDB

**Files:**
- Create: `src/invest_agent/data/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Candle` (Task 1).
- Produces: classe `CandleStore(root: Path)` com `append(candles: list[Candle]) -> int` (retorna nº de candles NOVOS; dedupe por `open_time`, o mais recente vence), `read(symbol, interval, start: datetime | None = None, end: datetime | None = None) -> list[Candle]` (ordenado por `open_time`; filtro `start` inclusivo, `end` exclusivo) e `latest_open_time(symbol, interval) -> datetime | None`. Layout em disco: `root/<symbol>/<interval>/<YYYY-MM>.parquet`. Task 7 consome exatamente estes nomes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store.py
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from invest_agent.data.models import Candle
from invest_agent.data.store import CandleStore


def _candle(hours: int, close: float = 1.5, symbol: str = "BTCUSDT") -> Candle:
    open_time = datetime(2024, 1, 31, 22, 0, tzinfo=timezone.utc) + timedelta(hours=hours)
    return Candle(symbol=symbol, interval="1h", open_time=open_time,
                  open=1.0, high=2.0, low=0.5, close=close, volume=10.0,
                  quote_volume=15.0, n_trades=3,
                  close_time=open_time + timedelta(minutes=59, seconds=59))


def test_roundtrip_append_read(tmp_path):
    store = CandleStore(tmp_path)
    candles = [_candle(i) for i in range(4)]
    assert store.append(candles) == 4
    out = store.read("BTCUSDT", "1h")
    assert out == sorted(candles, key=lambda c: c.open_time)


def test_particiona_por_mes(tmp_path):
    store = CandleStore(tmp_path)
    store.append([_candle(0), _candle(3)])  # 31/jan 22h e 01/fev 01h UTC
    files = sorted(p.name for p in (tmp_path / "BTCUSDT" / "1h").glob("*.parquet"))
    assert files == ["2024-01.parquet", "2024-02.parquet"]


def test_dedupe_por_open_time_o_mais_novo_vence(tmp_path):
    store = CandleStore(tmp_path)
    original = _candle(0)
    assert store.append([original]) == 1
    corrigido = replace(original, close=9.9)
    assert store.append([corrigido]) == 0  # mesmo open_time: zero novos
    out = store.read("BTCUSDT", "1h")
    assert len(out) == 1 and out[0].close == 9.9


def test_read_filtra_start_inclusivo_end_exclusivo(tmp_path):
    store = CandleStore(tmp_path)
    candles = [_candle(i) for i in range(4)]
    store.append(candles)
    out = store.read("BTCUSDT", "1h",
                     start=candles[1].open_time, end=candles[3].open_time)
    assert out == candles[1:3]


def test_latest_open_time(tmp_path):
    store = CandleStore(tmp_path)
    assert store.latest_open_time("BTCUSDT", "1h") is None
    candles = [_candle(i) for i in range(3)]
    store.append(candles)
    assert store.latest_open_time("BTCUSDT", "1h") == candles[-1].open_time


def test_read_de_simbolo_vazio(tmp_path):
    assert CandleStore(tmp_path).read("ETHUSDT", "1h") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/data/store.py
"""Armazenamento local de candles (spec §4.2): Parquet particionado por
símbolo/intervalo, um arquivo por mês, consultado via DuckDB. Escrita
atômica (tmp + rename); dedupe por open_time (o mais recente vence)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .models import Candle

_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("interval", pa.string()),
    ("open_time", pa.timestamp("us", tz="UTC")),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.float64()),
    ("quote_volume", pa.float64()),
    ("n_trades", pa.int64()),
    ("close_time", pa.timestamp("us", tz="UTC")),
])

_COLUMNS = [f.name for f in _SCHEMA]


def _to_table(rows: list[Candle]) -> pa.Table:
    return pa.table(
        {name: [getattr(c, name) for c in rows] for name in _COLUMNS},
        schema=_SCHEMA,
    )


def _read_file(path: Path) -> list[Candle]:
    return [Candle(**row) for row in pq.read_table(path).to_pylist()]


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    return con


class CandleStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, symbol: str, interval: str) -> Path:
        return self.root / symbol / interval

    def append(self, candles: list[Candle]) -> int:
        by_file: dict[Path, list[Candle]] = {}
        for c in candles:
            path = self._dir(c.symbol, c.interval) / f"{c.open_time:%Y-%m}.parquet"
            by_file.setdefault(path, []).append(c)
        added = 0
        for path, batch in by_file.items():
            merged: dict[datetime, Candle] = {}
            if path.exists():
                for row in _read_file(path):
                    merged[row.open_time] = row
            before = len(merged)
            for c in batch:
                merged[c.open_time] = c
            added += len(merged) - before
            rows = sorted(merged.values(), key=lambda c: c.open_time)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            pq.write_table(_to_table(rows), tmp)
            tmp.replace(path)
        return added

    def read(self, symbol: str, interval: str,
             start: datetime | None = None,
             end: datetime | None = None) -> list[Candle]:
        directory = self._dir(symbol, interval)
        if not any(directory.glob("*.parquet")):
            return []
        query = "SELECT * FROM read_parquet(?) WHERE 1=1"
        args: list = [str(directory / "*.parquet")]
        if start is not None:
            query += " AND open_time >= ?"
            args.append(start)
        if end is not None:
            query += " AND open_time < ?"
            args.append(end)
        query += " ORDER BY open_time"
        con = _connect()
        try:
            rows = con.execute(query, args).fetchall()
        finally:
            con.close()
        return [
            Candle(symbol=r[0], interval=r[1], open_time=_ensure_utc(r[2]),
                   open=r[3], high=r[4], low=r[5], close=r[6], volume=r[7],
                   quote_volume=r[8], n_trades=int(r[9]),
                   close_time=_ensure_utc(r[10]))
            for r in rows
        ]

    def latest_open_time(self, symbol: str, interval: str) -> datetime | None:
        directory = self._dir(symbol, interval)
        if not any(directory.glob("*.parquet")):
            return None
        con = _connect()
        try:
            (ts,) = con.execute(
                "SELECT max(open_time) FROM read_parquet(?)",
                [str(directory / "*.parquet")],
            ).fetchone()
        finally:
            con.close()
        return _ensure_utc(ts) if ts is not None else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: PASS (6 testes). Se algum teste de timezone falhar por o DuckDB devolver horário local, a causa provável é o `SET TimeZone='UTC'` ausente — ele é obrigatório.

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/data/store.py tests/test_store.py
git commit -m "feat: CandleStore em Parquet mensal com leitura via DuckDB"
```

---

### Task 5: Histórico mensal via data.binance.vision

**Files:**
- Create: `src/invest_agent/data/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: `Candle.from_kline_row` (Task 1).
- Produces: `month_range(start: date, end: date) -> list[tuple[int, int]]` (meses inclusivos) e `fetch_month(symbol: str, interval: str, year: int, month: int, transport: Callable[[str], bytes] | None = None) -> list[Candle] | None` (`None` quando o mês não existe — HTTP 404). Task 7 consome exatamente estes nomes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_history.py
import csv
import io
import urllib.error
import zipfile
from datetime import date

import pytest

from invest_agent.data.history import VISION_URL, fetch_month, month_range


def _zip_bytes(rows: list[list], name: str = "x.csv") -> bytes:
    text = io.StringIO()
    csv.writer(text).writerows(rows)
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as zf:
        zf.writestr(name, text.getvalue())
    return blob.getvalue()


ROW = [1704067200000, "1", "2", "0.5", "1.5", "10", 1704070799999,
       "15", 3, "5", "7.5", "0"]
HEADER = ["open_time", "open", "high", "low", "close", "volume",
          "close_time", "quote_volume", "count", "taker_buy_volume",
          "taker_buy_quote_volume", "ignore"]


def test_month_range_inclusivo_virando_ano():
    assert month_range(date(2023, 11, 5), date(2024, 2, 1)) == [
        (2023, 11), (2023, 12), (2024, 1), (2024, 2)]


def test_fetch_month_parseia_zip_e_monta_url():
    urls = []

    def transport(url: str) -> bytes:
        urls.append(url)
        return _zip_bytes([ROW])

    out = fetch_month("BTCUSDT", "1h", 2024, 1, transport=transport)
    assert len(out) == 1 and out[0].close == 1.5
    assert urls == [VISION_URL.format(symbol="BTCUSDT", interval="1h",
                                      year=2024, month=1)]


def test_fetch_month_ignora_linha_de_header():
    transport = lambda url: _zip_bytes([HEADER, ROW])
    out = fetch_month("BTCUSDT", "1h", 2024, 1, transport=transport)
    assert len(out) == 1


def test_fetch_month_404_devolve_none():
    def transport(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    assert fetch_month("BTCUSDT", "1h", 2016, 1, transport=transport) is None


def test_fetch_month_erro_nao_404_propaga():
    def transport(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 500, "boom", None, None)

    with pytest.raises(urllib.error.HTTPError):
        fetch_month("BTCUSDT", "1h", 2024, 1, transport=transport)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_history.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/data/history.py
"""Backfill de histórico via data.binance.vision (zips mensais de klines,
grátis, desde 2017 — spec §4.1). Arquivos de 2025+ têm header CSV e
timestamps em microssegundos; Candle.from_kline_row normaliza os µs e o
header é pulado aqui."""
from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
import zipfile
from datetime import date
from typing import Callable

from .models import Candle

VISION_URL = ("https://data.binance.vision/data/spot/monthly/klines/"
              "{symbol}/{interval}/{symbol}-{interval}-{year:04d}-{month:02d}.zip")


def _default_transport(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def month_range(start: date, end: date) -> list[tuple[int, int]]:
    """Todos os (ano, mês) de start a end, inclusivos."""
    out: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def fetch_month(symbol: str, interval: str, year: int, month: int,
                transport: Callable[[str], bytes] | None = None
                ) -> list[Candle] | None:
    """Baixa e parseia um zip mensal. None quando o mês não existe (404) —
    ex.: símbolo ainda não listado naquele mês."""
    transport = transport or _default_transport
    url = VISION_URL.format(symbol=symbol, interval=interval,
                            year=year, month=month)
    try:
        blob = transport(url)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        text = zf.read(zf.namelist()[0]).decode()
    out: list[Candle] = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip().isdigit():
            continue  # linha vazia ou header (arquivos 2025+)
        out.append(Candle.from_kline_row(symbol, interval, row))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_history.py -v`
Expected: PASS (5 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/data/history.py tests/test_history.py
git commit -m "feat: backfill mensal de candles via data.binance.vision"
```

---

### Task 6: `SymbolStats` reais para a whitelist

**Files:**
- Create: `src/invest_agent/data/symbol_stats.py`
- Test: `tests/test_symbol_stats.py`

**Interfaces:**
- Consumes: `SymbolStats` de `invest_agent.whitelist` (Fase 0 — campos exatos: `symbol, base, quote, quote_volume_30d, listed_days, is_leveraged`); `BinanceMarketData.exchange_info()` e `.klines()` (Task 3); `Candle` (Task 1).
- Produces: `fetch_symbol_stats(client, now: datetime, quote: str = "USDT") -> list[SymbolStats]`. O job semanal da Fase 2 fará `build_whitelist(fetch_symbol_stats(client, now))`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_symbol_stats.py
from datetime import datetime, timedelta, timezone

from invest_agent.data.models import Candle
from invest_agent.data.symbol_stats import fetch_symbol_stats
from invest_agent.whitelist import build_whitelist

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _daily(symbol: str, days_ago: int, quote_volume: float) -> Candle:
    open_time = NOW - timedelta(days=days_ago)
    return Candle(symbol=symbol, interval="1d", open_time=open_time,
                  open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0,
                  quote_volume=quote_volume, n_trades=3,
                  close_time=open_time + timedelta(days=1))


class FakeClient:
    def __init__(self):
        self.info = {"symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING",
             "baseAsset": "BTC", "quoteAsset": "USDT"},
            {"symbol": "NEWUSDT", "status": "TRADING",
             "baseAsset": "NEW", "quoteAsset": "USDT"},
            {"symbol": "ETHUPUSDT", "status": "TRADING",
             "baseAsset": "ETHUP", "quoteAsset": "USDT"},
            {"symbol": "XYZBRL", "status": "TRADING",
             "baseAsset": "XYZ", "quoteAsset": "BRL"},
            {"symbol": "OLDUSDT", "status": "BREAK",
             "baseAsset": "OLD", "quoteAsset": "USDT"},
        ]}
        # primeiro kline de cada símbolo (idade) e últimos 30 diários (volume)
        self.first = {
            "BTCUSDT": _daily("BTCUSDT", 3000, 1.0),
            "NEWUSDT": _daily("NEWUSDT", 90, 1.0),
            "ETHUPUSDT": _daily("ETHUPUSDT", 900, 1.0),
        }
        self.month_volume = {"BTCUSDT": 500.0, "NEWUSDT": 900.0, "ETHUPUSDT": 100.0}

    def exchange_info(self):
        return self.info

    def klines(self, symbol, interval, start=None, end=None, limit=1000):
        assert interval == "1d"
        if limit == 1:
            return [self.first[symbol]]
        return [_daily(symbol, d, self.month_volume[symbol]) for d in range(1, 31)]


def test_fetch_symbol_stats_filtra_e_calcula():
    stats = {s.symbol: s for s in fetch_symbol_stats(FakeClient(), NOW)}
    assert set(stats) == {"BTCUSDT", "NEWUSDT", "ETHUPUSDT"}  # BRL e BREAK fora
    assert stats["BTCUSDT"].listed_days == 3000
    assert stats["BTCUSDT"].quote_volume_30d == 500.0 * 30
    assert stats["ETHUPUSDT"].is_leveraged is True
    assert stats["NEWUSDT"].is_leveraged is False
    assert stats["BTCUSDT"].quote == "USDT"


def test_saida_alimenta_build_whitelist():
    wl = build_whitelist(fetch_symbol_stats(FakeClient(), NOW), size=20)
    # NEWUSDT tem 90 dias (< 365) e ETHUPUSDT é alavancado: só BTC entra
    # pelo critério; ETHUSDT entra por ALWAYS_INCLUDED.
    assert wl == frozenset({"BTCUSDT", "ETHUSDT"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_symbol_stats.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/data/symbol_stats.py
"""Coleta as estatísticas reais que alimentam build_whitelist (Fase 0,
spec §4.4): volume USDT de 30 dias e idade de listagem, via API pública.
Pensado para o job semanal da Fase 2: build_whitelist(fetch_symbol_stats(...))."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..whitelist import SymbolStats

_EPOCH = datetime(2017, 1, 1, tzinfo=timezone.utc)  # Binance abriu em 2017
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


def _is_leveraged(base: str) -> bool:
    return base.endswith(LEVERAGED_SUFFIXES)


def fetch_symbol_stats(client, now: datetime,
                       quote: str = "USDT") -> list[SymbolStats]:
    out: list[SymbolStats] = []
    for entry in client.exchange_info()["symbols"]:
        if entry["status"] != "TRADING" or entry["quoteAsset"] != quote:
            continue
        symbol, base = entry["symbol"], entry["baseAsset"]
        first = client.klines(symbol, "1d", start=_EPOCH, limit=1)
        if not first:
            continue
        listed_days = (now - first[0].open_time).days
        last30 = client.klines(symbol, "1d",
                               start=now - timedelta(days=31), end=now, limit=31)
        volume_30d = sum(c.quote_volume for c in last30)
        out.append(SymbolStats(symbol=symbol, base=base, quote=quote,
                               quote_volume_30d=volume_30d,
                               listed_days=listed_days,
                               is_leveraged=_is_leveraged(base)))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_symbol_stats.py -v`
Expected: PASS (2 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/data/symbol_stats.py tests/test_symbol_stats.py
git commit -m "feat: coleta de SymbolStats reais para a whitelist dinâmica"
```

---

### Task 7: Orquestração `ensure_history` + CLI

**Files:**
- Create: `src/invest_agent/data/ingest.py`
- Test: `tests/test_ingest.py`
- Modify: `README.md` (seção de uso)

**Interfaces:**
- Consumes: `CandleStore` (Task 4), `BinanceMarketData.klines_range` (Task 3), `fetch_month`/`month_range` (Task 5).
- Produces: `ensure_history(store, client, symbol, interval, since: datetime, now: datetime, fetch_month_fn=fetch_month, transport=None) -> int` (nº de candles novos; idempotente) e `main(argv: list[str] | None = None)` executável via `python3 -m invest_agent.data.ingest`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest.py
from datetime import datetime, timedelta, timezone

from invest_agent.data.ingest import ensure_history
from invest_agent.data.models import Candle
from invest_agent.data.store import CandleStore


def _candle(open_time: datetime, symbol: str = "BTCUSDT") -> Candle:
    return Candle(symbol=symbol, interval="1h", open_time=open_time,
                  open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0,
                  quote_volume=15.0, n_trades=3,
                  close_time=open_time + timedelta(minutes=59, seconds=59))


SINCE = datetime(2024, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2024, 3, 10, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, candles):
        self.candles = candles
        self.calls = []

    def klines_range(self, symbol, interval, start, end, limit=1000):
        self.calls.append((start, end))
        return [c for c in self.candles if start <= c.open_time < end]


def test_backfill_inicial_meses_mais_cauda_rest(tmp_path):
    store = CandleStore(tmp_path)
    month_calls = []

    def fake_fetch_month(symbol, interval, year, month, transport=None):
        month_calls.append((year, month))
        if (year, month) == (2024, 1):
            return None  # mês sem arquivo (ex.: símbolo listado depois)
        return [_candle(datetime(year, month, 1, tzinfo=timezone.utc))]

    tail = [_candle(datetime(2024, 3, 10, 10, 0, tzinfo=timezone.utc))]
    client = FakeClient(tail)
    added = ensure_history(store, client, "BTCUSDT", "1h", SINCE, NOW,
                           fetch_month_fn=fake_fetch_month)
    # meses até o ANTERIOR ao corrente: jan e fev; cauda REST cobre março
    assert month_calls == [(2024, 1), (2024, 2)]
    assert added == 2  # fev (jan era None) + 1 da cauda
    assert len(store.read("BTCUSDT", "1h")) == 2


def test_atualizacao_incremental_nao_baixa_meses(tmp_path):
    store = CandleStore(tmp_path)
    existing = _candle(datetime(2024, 3, 10, 9, 0, tzinfo=timezone.utc))
    store.append([existing])
    novo = _candle(datetime(2024, 3, 10, 10, 0, tzinfo=timezone.utc))
    client = FakeClient([existing, novo])

    def explode(*a, **k):
        raise AssertionError("não deve baixar meses quando o store já tem dados")

    added = ensure_history(store, client, "BTCUSDT", "1h", SINCE, NOW,
                           fetch_month_fn=explode)
    assert added == 1
    start_used = client.calls[0][0]
    assert start_used > existing.open_time  # não re-baixa o último candle


def test_idempotente(tmp_path):
    store = CandleStore(tmp_path)
    candles = [_candle(datetime(2024, 3, 10, h, 0, tzinfo=timezone.utc))
               for h in range(3)]
    client = FakeClient(candles)
    first = ensure_history(store, client, "BTCUSDT", "1h", SINCE, NOW,
                           fetch_month_fn=lambda *a, **k: None)
    second = ensure_history(store, client, "BTCUSDT", "1h", SINCE, NOW,
                            fetch_month_fn=lambda *a, **k: None)
    assert first == 3 and second == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ingest.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/data/ingest.py
"""Backfill + atualização incremental de candles, idempotente: primeiro
carregamento via zips mensais do data.binance.vision, cauda e updates via
REST. O main() do CLI é a ÚNICA borda do projeto que lê o relógio e usa a
rede reais — toda a lógica recebe now/transport injetados."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .binance_client import BinanceMarketData
from .history import fetch_month, month_range
from .store import CandleStore


def ensure_history(store: CandleStore, client, symbol: str, interval: str,
                   since: datetime, now: datetime,
                   fetch_month_fn=fetch_month, transport=None) -> int:
    """Garante candles de `since` até `now` no store. Retorna o nº de
    candles novos. Store vazio: backfill mensal até o mês ANTERIOR ao de
    `now` + cauda REST. Store com dados: só a cauda REST a partir do último
    candle (os zips mensais não são re-baixados)."""
    added = 0
    if store.latest_open_time(symbol, interval) is None:
        last_full_month_end = date(now.year, now.month, 1) - timedelta(days=1)
        for year, month in month_range(since.date(), last_full_month_end):
            candles = fetch_month_fn(symbol, interval, year, month,
                                     transport=transport)
            if candles:
                added += store.append(candles)
    latest = store.latest_open_time(symbol, interval)
    start = latest + timedelta(milliseconds=1) if latest is not None else since
    tail = client.klines_range(symbol, interval, start=start, end=now)
    if tail:
        added += store.append(tail)
    return added


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Backfill e atualização de candles da Binance")
    parser.add_argument("--symbol", required=True, help="ex.: BTCUSDT")
    parser.add_argument("--interval", default="1h", help="ex.: 1h, 4h, 1d")
    parser.add_argument("--since", default="2024-01-01",
                        help="data inicial UTC (YYYY-MM-DD)")
    parser.add_argument("--root", default=Path("data/candles"), type=Path)
    args = parser.parse_args(argv)

    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)  # borda de composição: único lugar com relógio real
    store = CandleStore(args.root)
    client = BinanceMarketData()
    added = ensure_history(store, client, args.symbol, args.interval, since, now)
    print(f"{args.symbol} {args.interval}: {added} candles novos em {args.root}")


if __name__ == "__main__":
    main()
```

No `README.md`, acrescentar após a seção "🧪 Rodar os testes":

```markdown
## 📥 Ingestão de candles (Fase 1)

```bash
python3 -m invest_agent.data.ingest --symbol BTCUSDT --interval 1h --since 2024-01-01
```

Backfill histórico via [data.binance.vision](https://data.binance.vision)
(grátis) + cauda recente via REST público. Idempotente: rodar de novo só
baixa o que falta. Os dados ficam em `data/candles/` (fora do git),
particionados em Parquet mensal e consultáveis com DuckDB.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ingest.py -v`
Expected: PASS (3 testes). Rodar a suíte completa: `python3 -m pytest` — tudo verde, sem warnings.

- [ ] **Step 5: Smoke test manual do CLI (com rede, fora dos testes)**

Run: `python3 -m invest_agent.data.ingest --symbol BTCUSDT --interval 1h --since 2026-08-01 && python3 -m invest_agent.data.ingest --symbol BTCUSDT --interval 1h --since 2026-08-01`
Expected: primeira execução imprime N > 0 candles novos; a segunda imprime 0 (idempotência real). Se a rede estiver indisponível, registrar isso no report e seguir (os testes unitários são o gate).

- [ ] **Step 6: Commit**

```bash
git add src/invest_agent/data/ingest.py tests/test_ingest.py README.md
git commit -m "feat: ensure_history idempotente + CLI de ingestão de candles"
```

---

## Self-review do plano (executada na escrita)

- **Cobertura da spec (escopo 1a):** candles REST §4.1 → T3; histórico data.binance.vision §4.1 → T5; Parquet+DuckDB §4.2 → T4; indicadores em Python §4.3 → T2; whitelist dinâmica com dados reais §4.4 → T6; cron de ingestão → CLI T7 (o agendamento em si é infra da Fase 2). RSS/F&G/BCB (§4.1) e SQLite de decisões (§4.2) ficam para o plano 1c; backtest (§5 fase 1) para o 1b — decisão de decomposição registrada no cabeçalho.
- **Placeholders:** nenhum TBD/TODO; todo step de código tem o código completo.
- **Consistência de tipos:** `Candle` campos idênticos em T1/T4 (`_COLUMNS` deriva do schema com os mesmos nomes dos atributos); `from_kline_row` usado em T3/T5; `SymbolStats(symbol, base, quote, quote_volume_30d, listed_days, is_leveraged)` confere com `src/invest_agent/whitelist.py:18-24`; `fetch_month(symbol, interval, year, month, transport)` idêntico em T5/T7; `klines_range(symbol, interval, start, end, limit)` idêntico em T3/T7; `append -> int (novos)` usado por T7 nos asserts de idempotência.
