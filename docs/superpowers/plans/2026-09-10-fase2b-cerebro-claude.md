# Fase 2b — Cérebro Claude — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O loop de decisão com LLM da spec §4.3: Haiku 4.5 tria a whitelist (≤3 candidatos), Sonnet 5 produz a `Proposal` tipada via structured outputs (falha/refusal = ciclo sem ação), notícias são enriquecidas em batch (resumo/sentimento/materialidade), custos de API rastreados por chamada (alimentam o breaker de custo da 2a), e a revisão semanal do Opus 5 via Batch API escreve em `learnings/`.

**Architecture:** Módulo novo `src/invest_agent/brain/` com um wrapper de LLM testável (`create_fn` injetável — NENHUM teste chama a API real), triagem, proposer, enriquecimento e revisão semanal. O `SqliteStore` ganha leitura/atualização de notícias (as colunas LLM da 1c saem do NULL) e índices. O ciclo da 2a ganha a flag `--llm` que troca o `hold_proposer` pelo proposer real e liga notícias/macro no contexto.

**Tech Stack:** Python ≥ 3.12; runtime: duckdb, pyarrow, backtrader + **`anthropic`** (SDK oficial — quarta e última dep de runtime); dev: pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-invest-agent-design.md` (§4.3 cérebro, §4.2 learnings, §2 modelos). **Rulings do controller (no ledger):**
1. **Modelos exatamente como a spec §2:** decisão `claude-sonnet-5`, triagem `claude-haiku-4-5`, revisão semanal `claude-opus-5` (Batch API, −50%).
2. **Structured outputs** (`output_config.format` com json_schema strict) em vez de prompt-parsing — a validação vira responsabilidade da API; `conviction` 0-1 é re-validada em código (schema não suporta min/max) e o `Proposal.__post_init__` da Fase 0 é a última linha.
3. **Refusal/parse-error = HOLD** ("falha = ciclo sem ação", spec §4.3 item 4) — nunca exceção que derrube o ciclo.
4. **Custo por chamada calculado da `usage`** com tabela de preços de lista (Sonnet 5 $3/$15, Haiku $1/$5, Opus $5/$25 por MTok; cache read 0.1×, cache write 2× p/ TTL 1h) — aproximação documentada, suficiente para o breaker de custo diário.
5. **Enriquecimento em UMA chamada Haiku com array** (não Batch API): o volume por ciclo é pequeno; Batch API fica só para a revisão semanal, onde a spec a mandata.

## Global Constraints

- Runtime deps ficam exatamente duckdb, pyarrow, backtrader, anthropic (Task 1 adiciona `anthropic>=0.40`).
- **NENHUM teste chama a API da Anthropic:** todo acesso passa por `create_fn`/`batches` injetáveis; testes usam fakes com objetos de resposta simulados.
- A chave (`settings.anthropic_api_key`) só é lida na borda (composição no `main()`); o LLM NUNCA vê chaves da Binance, saldos editáveis ou qualquer caminho de escrita — recebe o dict de contexto pronto (read-only, spec §3).
- Falha de LLM (refusal, JSON inválido, erro de rede) NUNCA derruba o ciclo: vira HOLD com o motivo no rationale.
- Todo custo de chamada é registrado via `store.add_api_cost(day, usd)` no dia UTC de `now`.
- Prompts e mensagens em português; notícias entram como campos estruturados, nunca texto bruto concatenado (mitigação de prompt injection, spec §4.3).
- Datas UTC; `now` parâmetro; commits pequenos, sem assinatura.

---

### Task 1: Wrapper LLM testável + custo por chamada

**Files:**
- Modify: `pyproject.toml` (adicionar `anthropic>=0.40`)
- Create: `src/invest_agent/brain/__init__.py`
- Create: `src/invest_agent/brain/client.py`
- Test: `tests/test_brain_client.py`

**Interfaces:**
- Produces: `PRICES: dict[str, tuple[float, float]]` (modelo → USD por MTok de input/output: `{"claude-sonnet-5": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0), "claude-opus-5": (5.0, 25.0)}`); `usage_cost_usd(model: str, usage) -> float`; `LlmError(Exception)`; classe `LlmClient(create_fn: Callable | None = None, api_key: str = "")` com método `structured(model: str, system: str, user_payload: dict, schema: dict, max_tokens: int, cache_ttl: str = "1h") -> tuple[dict | None, float]` — retorna `(dados_parseados, custo_usd)`; em refusal ou JSON inválido retorna `(None, custo)`; erro de transporte/API levanta `LlmError` (o chamador converte em HOLD). O `system` vai como bloco único com `cache_control {type: ephemeral, ttl}`; o `user_payload` vira `json.dumps(..., sort_keys=True, ensure_ascii=False)` numa mensagem user. Tasks 3-7 consomem.

- [ ] **Step 0: Instalar e fixar a dependência**

```bash
python3 -m pip install "anthropic>=0.40"
```

Em `pyproject.toml`: `dependencies = ["duckdb>=1.1", "pyarrow>=17", "backtrader>=1.9.78", "anthropic>=0.40"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_client.py
import json
from types import SimpleNamespace

import pytest

from invest_agent.brain.client import (
    PRICES, LlmClient, LlmError, usage_cost_usd,
)

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}},
          "required": ["x"], "additionalProperties": False}


def _usage(inp=1000, out=500, cache_read=0, cache_write=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_read_input_tokens=cache_read,
                           cache_creation_input_tokens=cache_write)


def _response(text='{"x": 1}', stop_reason="end_turn", usage=None):
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], stop_reason=stop_reason,
                           usage=usage or _usage())


def test_usage_cost_usd_haiku():
    # 1000 in + 500 out no haiku: 1000/1e6*1.0 + 500/1e6*5.0 = 0.0035
    assert usage_cost_usd("claude-haiku-4-5", _usage()) == pytest.approx(0.0035)


def test_usage_cost_usd_com_cache():
    # sonnet: in 1000*3/1e6 + out 500*15/1e6 + read 2000*0.1*3/1e6 + write 1000*2*3/1e6
    usage = _usage(1000, 500, cache_read=2000, cache_write=1000)
    esperado = (1000 * 3.0 + 500 * 15.0 + 2000 * 0.3 + 1000 * 6.0) / 1e6
    assert usage_cost_usd("claude-sonnet-5", usage) == pytest.approx(esperado)


def test_structured_parseia_e_custa():
    calls = []

    def create_fn(**kwargs):
        calls.append(kwargs)
        return _response()

    client = LlmClient(create_fn=create_fn)
    data, cost = client.structured("claude-haiku-4-5", "sistema",
                                   {"b": 2, "a": 1}, SCHEMA, max_tokens=500)
    assert data == {"x": 1} and cost == pytest.approx(0.0035)
    (kwargs,) = calls
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["max_tokens"] == 500
    assert kwargs["output_config"] == {"format": {"type": "json_schema",
                                                  "schema": SCHEMA}}
    system_block = kwargs["system"][0]
    assert system_block["text"] == "sistema"
    assert system_block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    user_text = kwargs["messages"][0]["content"]
    assert json.loads(user_text) == {"a": 1, "b": 2}
    assert user_text.index('"a"') < user_text.index('"b"')  # sort_keys


def test_structured_refusal_vira_none_com_custo():
    client = LlmClient(create_fn=lambda **k: _response(
        text="", stop_reason="refusal"))
    data, cost = client.structured("claude-sonnet-5", "s", {}, SCHEMA, 100)
    assert data is None and cost > 0


def test_structured_json_invalido_vira_none():
    client = LlmClient(create_fn=lambda **k: _response(text="não é json"))
    data, cost = client.structured("claude-haiku-4-5", "s", {}, SCHEMA, 100)
    assert data is None and cost > 0


def test_erro_de_api_vira_llm_error():
    def create_fn(**kwargs):
        raise RuntimeError("boom")

    client = LlmClient(create_fn=create_fn)
    with pytest.raises(LlmError, match="falha na chamada"):
        client.structured("claude-haiku-4-5", "s", {}, SCHEMA, 100)


def test_precos_da_spec():
    assert PRICES["claude-sonnet-5"] == (3.0, 15.0)
    assert PRICES["claude-haiku-4-5"] == (1.0, 5.0)
    assert PRICES["claude-opus-5"] == (5.0, 25.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_brain_client.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'invest_agent.brain'`

- [ ] **Step 3: Write minimal implementation**

`src/invest_agent/brain/__init__.py`: vazio.

```python
# src/invest_agent/brain/client.py
"""Wrapper testável da API da Claude. create_fn injetável — nenhum teste
toca a rede. Structured outputs (output_config.format) garantem JSON
válido; refusal ou parse inválido viram (None, custo) — 'falha = ciclo
sem ação' (spec §4.3). Custo aproximado da usage com preços de lista:
cache read 0.1×, cache write 2× (TTL 1h)."""
from __future__ import annotations

import json
from typing import Callable

PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-5": (5.0, 25.0),
}

_CACHE_READ_MULT = 0.1
_CACHE_WRITE_MULT = 2.0  # TTL 1h


class LlmError(Exception):
    """Falha de transporte/API — o chamador converte em HOLD."""


def usage_cost_usd(model: str, usage) -> float:
    price_in, price_out = PRICES[model]
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (usage.input_tokens * price_in
            + usage.output_tokens * price_out
            + cache_read * _CACHE_READ_MULT * price_in
            + cache_write * _CACHE_WRITE_MULT * price_in) / 1_000_000


def _default_create_fn(api_key: str) -> Callable:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    return client.messages.create


class LlmClient:
    def __init__(self, create_fn: Callable | None = None, api_key: str = ""):
        self._create = create_fn or _default_create_fn(api_key)

    def structured(self, model: str, system: str, user_payload: dict,
                   schema: dict, max_tokens: int,
                   cache_ttl: str = "1h") -> tuple[dict | None, float]:
        try:
            response = self._create(
                model=model,
                max_tokens=max_tokens,
                system=[{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral", "ttl": cache_ttl},
                }],
                messages=[{
                    "role": "user",
                    "content": json.dumps(user_payload, sort_keys=True,
                                          ensure_ascii=False),
                }],
                output_config={"format": {"type": "json_schema",
                                          "schema": schema}},
            )
        except Exception as err:
            raise LlmError(f"falha na chamada ao modelo {model}: {err}") from err
        cost = usage_cost_usd(model, response.usage)
        if response.stop_reason == "refusal":
            return None, cost
        text = next((b.text for b in response.content
                     if getattr(b, "type", "") == "text"), "")
        try:
            return json.loads(text), cost
        except (json.JSONDecodeError, TypeError):
            return None, cost
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_brain_client.py -v`
Expected: PASS (7 testes).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/invest_agent/brain/ tests/test_brain_client.py
git commit -m "feat: wrapper LLM testável com structured outputs e custo por chamada"
```

---

### Task 2: Store — leitura/enriquecimento de notícias + índices

**Files:**
- Modify: `src/invest_agent/storage/sqlite_store.py`
- Test: `tests/test_sqlite_news_ops.py`

**Interfaces:**
- Consumes: schema `news` existente (colunas `summary_llm`, `sentiment`, `materiality` já existem, NULL).
- Produces (métodos novos): `unenriched_news(limit: int = 20) -> list[tuple[str, str, str]]` (id, title, summary — só linhas com `summary_llm IS NULL`, mais antigas primeiro); `set_news_enrichment(news_id: str, summary_llm: str, sentiment: str, materiality: int) -> None`; `recent_news(since: datetime, limit: int = 15) -> list[tuple[str, str, tuple[str, ...], str | None, str | None, int | None]]` (title, source, assets, published_at ISO, sentiment, materiality — só notícias com `ingested_at >= since`, mais novas primeiro); índices novos `idx_news_url`, `idx_news_simhash`, `idx_decision_ts` (fecha minors deferidos da 1c). NÃO tocar no decision_log nem em nada existente. Tasks 5 e 6 consomem.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sqlite_news_ops.py
from datetime import datetime, timedelta, timezone

from invest_agent.news.models import make_news_item
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _insert(store, title, url, hours_ago=1, assets=("BTCUSDT",)):
    item = make_news_item("src", title, url,
                          NOW - timedelta(hours=hours_ago),
                          NOW - timedelta(hours=hours_ago), "resumo")
    store.insert_news([(item, assets)])
    return item


def test_unenriched_e_set_enrichment(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    velho = _insert(store, "t-velho", "https://x.com/1", hours_ago=5)
    novo = _insert(store, "t-novo", "https://x.com/2", hours_ago=1)
    pend = store.unenriched_news()
    assert [p[0] for p in pend] == [velho.id, novo.id]  # antigas primeiro
    assert pend[0][1] == "t-velho" and pend[0][2] == "resumo"
    store.set_news_enrichment(velho.id, "resumo llm", "negativo", 4)
    assert [p[0] for p in store.unenriched_news()] == [novo.id]
    store.close()


def test_unenriched_respeita_limit(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    for i in range(5):
        _insert(store, f"t{i}", f"https://x.com/{i}", hours_ago=5 - i)
    assert len(store.unenriched_news(limit=3)) == 3
    store.close()


def test_recent_news_filtra_e_ordena(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    recente = _insert(store, "recente", "https://x.com/1", hours_ago=2)
    _insert(store, "antiga", "https://x.com/2", hours_ago=48)
    store.set_news_enrichment(recente.id, "s", "positivo", 3)
    out = store.recent_news(since=NOW - timedelta(hours=24))
    assert len(out) == 1
    title, source, assets, published_at, sentiment, materiality = out[0]
    assert title == "recente" and source == "src"
    assert assets == ("BTCUSDT",)
    assert sentiment == "positivo" and materiality == 3
    store.close()


def test_indices_criados(tmp_path):
    import sqlite3
    store = SqliteStore(tmp_path / "a.db")
    store.close()
    con = sqlite3.connect(tmp_path / "a.db")
    nomes = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    con.close()
    assert {"idx_news_url", "idx_news_simhash", "idx_decision_ts"} <= nomes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sqlite_news_ops.py -v`
Expected: FAIL com `AttributeError`

- [ ] **Step 3: Write minimal implementation**

Acrescentar ao `_SCHEMA`:

```sql
CREATE INDEX IF NOT EXISTS idx_news_url ON news(url);
CREATE INDEX IF NOT EXISTS idx_news_simhash ON news(simhash);
CREATE INDEX IF NOT EXISTS idx_decision_ts ON decision_log(ts);
```

E os métodos (estilo do módulo):

```python
    # --- enriquecimento LLM de notícias (Fase 2b) ---

    def unenriched_news(self, limit: int = 20) -> list[tuple[str, str, str]]:
        rows = self._con.execute(
            "SELECT id, title, summary FROM news WHERE summary_llm IS NULL"
            " ORDER BY ingested_at LIMIT ?", (limit,)).fetchall()
        return [tuple(r) for r in rows]

    def set_news_enrichment(self, news_id: str, summary_llm: str,
                            sentiment: str, materiality: int) -> None:
        self._con.execute(
            "UPDATE news SET summary_llm=?, sentiment=?, materiality=?"
            " WHERE id=?", (summary_llm, sentiment, materiality, news_id))
        self._con.commit()

    def recent_news(self, since: datetime, limit: int = 15) -> list[tuple]:
        rows = self._con.execute(
            "SELECT title, source, assets, published_at, sentiment,"
            " materiality FROM news WHERE ingested_at >= ?"
            " ORDER BY ingested_at DESC LIMIT ?",
            (since.isoformat(), limit)).fetchall()
        return [(r[0], r[1], tuple(a for a in r[2].split(",") if a),
                 r[3], r[4], r[5]) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sqlite_news_ops.py tests/test_sqlite_store.py tests/test_sqlite_ops.py -v`
Expected: PASS (novos + antigos intactos).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/storage/sqlite_store.py tests/test_sqlite_news_ops.py
git commit -m "feat: leitura/enriquecimento de notícias no store + índices"
```

---

### Task 3: Triagem Haiku (≤3 candidatos da whitelist)

**Files:**
- Create: `src/invest_agent/brain/triage.py`
- Test: `tests/test_brain_triage.py`

**Interfaces:**
- Consumes: `LlmClient` (Task 1).
- Produces: `TRIAGE_MODEL = "claude-haiku-4-5"`, `TRIAGE_SCHEMA` (object com `candidates`: array de objects `{symbol: string, reason: string}`, `required`, `additionalProperties: false`), `TRIAGE_SYSTEM` (português) e `triage_candidates(client: LlmClient, context: dict) -> tuple[list[str], float]` — retorna (símbolos, custo). Pós-processamento em código: só símbolos presentes em `context["whitelist"]`, dedupe preservando ordem, máximo 3; `None` do client (refusal/parse) → lista vazia. Task 4/6 consomem.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_triage.py
from invest_agent.brain.client import LlmClient
from invest_agent.brain.triage import (
    TRIAGE_MODEL, TRIAGE_SCHEMA, triage_candidates,
)
from types import SimpleNamespace
import json


def _client(payload, stop_reason="end_turn"):
    def create_fn(**kwargs):
        create_fn.kwargs = kwargs
        block = SimpleNamespace(type="text", text=json.dumps(payload))
        usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(content=[block], stop_reason=stop_reason,
                               usage=usage)
    client = LlmClient(create_fn=create_fn)
    client._fn = create_fn
    return client


CTX = {"whitelist": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "symbols": {}}


def test_triagem_filtra_whitelist_dedupe_e_limita_a_3():
    payload = {"candidates": [
        {"symbol": "BTCUSDT", "reason": "a"},
        {"symbol": "FORAUSDT", "reason": "b"},   # fora da whitelist
        {"symbol": "BTCUSDT", "reason": "c"},    # duplicado
        {"symbol": "ETHUSDT", "reason": "d"},
        {"symbol": "SOLUSDT", "reason": "e"},
    ]}
    client = _client(payload)
    symbols, cost = triage_candidates(client, CTX)
    assert symbols == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert cost > 0
    assert client._fn.kwargs["model"] == TRIAGE_MODEL


def test_triagem_refusal_vira_lista_vazia():
    client = _client({}, stop_reason="refusal")
    symbols, cost = triage_candidates(client, CTX)
    assert symbols == [] and cost > 0


def test_schema_e_strito():
    assert TRIAGE_SCHEMA["additionalProperties"] is False
    item = TRIAGE_SCHEMA["properties"]["candidates"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"symbol", "reason"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_brain_triage.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/brain/triage.py
"""Passo 2 do ciclo (spec §4.3): Haiku 4.5 tria a whitelist e devolve até
3 candidatos. O código impõe as regras duras (whitelist, dedupe, teto de
3) — o LLM só ordena por interesse; refusal/parse inválido = sem
candidatos (ciclo tende a HOLD)."""
from __future__ import annotations

from .client import LlmClient

TRIAGE_MODEL = "claude-haiku-4-5"

TRIAGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["symbol", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

TRIAGE_SYSTEM = (
    "Você é o triador de um agente de investimento em cripto (spot, "
    "Binance). Receberá um contexto JSON com a whitelist, indicadores por "
    "símbolo, notícias enriquecidas e macro. Escolha até 3 símbolos DA "
    "WHITELIST que mereçam análise neste ciclo (momentum, notícia "
    "material, movimento relevante), do mais ao menos interessante, cada "
    "um com uma razão curta em português. Sem sinal claro, devolva lista "
    "vazia — não force candidatos."
)


def triage_candidates(client: LlmClient,
                      context: dict) -> tuple[list[str], float]:
    data, cost = client.structured(TRIAGE_MODEL, TRIAGE_SYSTEM, context,
                                   TRIAGE_SCHEMA, max_tokens=1000)
    if not data:
        return [], cost
    whitelist = set(context.get("whitelist", []))
    out: list[str] = []
    for entry in data.get("candidates", []):
        symbol = entry.get("symbol")
        if symbol in whitelist and symbol not in out:
            out.append(symbol)
        if len(out) == 3:
            break
    return out, cost
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_brain_triage.py -v`
Expected: PASS (3 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/brain/triage.py tests/test_brain_triage.py
git commit -m "feat: triagem Haiku da whitelist (≤3 candidatos, regras no código)"
```

---

### Task 4: Proposer Sonnet + composição com custos

**Files:**
- Create: `src/invest_agent/brain/proposer.py`
- Test: `tests/test_brain_proposer.py`

**Interfaces:**
- Consumes: `LlmClient`/`LlmError` (Task 1), `triage_candidates` (Task 3), `Proposal`/`Action` (Fase 0), `SqliteStore.add_api_cost` (2a).
- Produces: `PROPOSER_MODEL = "claude-sonnet-5"`, `PROPOSAL_SCHEMA` (object: `symbol` string, `action` enum ["buy","sell","close","hold"], `conviction` number, `rationale` string, `urgency` enum ["baixa","media","alta"]; required todos; additionalProperties false), `PROPOSER_SYSTEM` (português — "você propõe, código dispõe", default manter), `propose(client, context, candidates) -> tuple[Proposal, float]` (fallbacks: sem candidatos → HOLD; refusal/parse → HOLD; conviction fora de 0..1 → clamp para dentro ANTES de construir o Proposal; símbolo fora de candidates → HOLD com motivo) e `make_llm_proposer(client: LlmClient, store, now_provider: Callable[[], datetime]) -> Proposer` — closure compatível com `Proposer` da 2a que roda triagem+proposta, soma os custos e grava `store.add_api_cost(now_provider().date(), custo_total)`; `LlmError` em qualquer etapa → HOLD com rationale "falha de LLM: ...". Task 6 consome.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_proposer.py
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from invest_agent.brain.client import LlmClient, LlmError
from invest_agent.brain.proposer import (
    PROPOSAL_SCHEMA, PROPOSER_MODEL, make_llm_proposer, propose,
)
from invest_agent.models import Action
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
CTX = {"cycle_id": "2026091012", "whitelist": ["BTCUSDT", "ETHUSDT"],
       "symbols": {}}


def _client(payloads):
    """payloads: lista de respostas em ordem de chamada (dict, 'refusal'
    ou exceção)."""
    state = {"i": 0}

    def create_fn(**kwargs):
        payload = payloads[state["i"]]
        state["i"] += 1
        if isinstance(payload, Exception):
            raise payload
        usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if payload == "refusal":
            return SimpleNamespace(content=[], stop_reason="refusal",
                                   usage=usage)
        block = SimpleNamespace(type="text", text=json.dumps(payload))
        return SimpleNamespace(content=[block], stop_reason="end_turn",
                               usage=usage)

    return LlmClient(create_fn=create_fn)


def test_propose_feliz():
    client = _client([{"symbol": "BTCUSDT", "action": "buy",
                       "conviction": 0.4, "rationale": "momentum",
                       "urgency": "media"}])
    proposal, cost = propose(client, CTX, ["BTCUSDT"])
    assert proposal.action is Action.BUY and proposal.symbol == "BTCUSDT"
    assert proposal.conviction == 0.4 and proposal.cycle_id == "2026091012"
    assert cost > 0


def test_propose_sem_candidatos_e_hold_sem_chamada():
    client = _client([])  # qualquer chamada estouraria IndexError
    proposal, cost = propose(client, CTX, [])
    assert proposal.action is Action.HOLD and cost == 0.0


def test_propose_refusal_vira_hold():
    client = _client(["refusal"])
    proposal, _ = propose(client, CTX, ["BTCUSDT"])
    assert proposal.action is Action.HOLD
    assert "sem ação" in proposal.rationale


def test_propose_simbolo_fora_dos_candidatos_vira_hold():
    client = _client([{"symbol": "ETHUSDT", "action": "buy",
                       "conviction": 0.5, "rationale": "x",
                       "urgency": "alta"}])
    proposal, _ = propose(client, CTX, ["BTCUSDT"])
    assert proposal.action is Action.HOLD


def test_propose_conviction_fora_da_faixa_e_clampada():
    client = _client([{"symbol": "BTCUSDT", "action": "buy",
                       "conviction": 1.7, "rationale": "x",
                       "urgency": "alta"}])
    proposal, _ = propose(client, CTX, ["BTCUSDT"])
    assert proposal.conviction == 1.0


def test_make_llm_proposer_grava_custo_e_propaga(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    # 1ª chamada: triagem devolve BTCUSDT; 2ª: proposta buy
    client = _client([
        {"candidates": [{"symbol": "BTCUSDT", "reason": "r"}]},
        {"symbol": "BTCUSDT", "action": "buy", "conviction": 0.3,
         "rationale": "x", "urgency": "baixa"},
    ])
    proposer = make_llm_proposer(client, store, lambda: NOW)
    proposal = proposer(CTX)
    assert proposal.action is Action.BUY
    assert store.api_cost_today(NOW.date()) > 0
    store.close()


def test_make_llm_proposer_llm_error_vira_hold(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    client = _client([RuntimeError("rede caiu")])
    proposer = make_llm_proposer(client, store, lambda: NOW)
    proposal = proposer(CTX)
    assert proposal.action is Action.HOLD
    assert "falha de LLM" in proposal.rationale
    store.close()


def test_schema_do_proposal():
    props = PROPOSAL_SCHEMA["properties"]
    assert props["action"]["enum"] == ["buy", "sell", "close", "hold"]
    assert set(PROPOSAL_SCHEMA["required"]) == {
        "symbol", "action", "conviction", "rationale", "urgency"}
    assert PROPOSAL_SCHEMA["additionalProperties"] is False
    assert PROPOSER_MODEL == "claude-sonnet-5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_brain_proposer.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/brain/proposer.py
"""Passo 3 do ciclo (spec §4.3): Sonnet 5 produz a Proposal tipada.
O LLM NUNCA vê chaves, nunca calcula tamanho de posição e o estado é
read-only (spec §3): recebe um dict de contexto pronto e devolve
{ativo, ação, convicção, racional, urgência}. Toda falha vira HOLD —
o ciclo nunca cai por causa do LLM."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from ..models import Action, Proposal
from .client import LlmClient, LlmError
from .triage import triage_candidates

PROPOSER_MODEL = "claude-sonnet-5"

PROPOSAL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "action": {"type": "string",
                   "enum": ["buy", "sell", "close", "hold"]},
        "conviction": {"type": "number"},
        "rationale": {"type": "string"},
        "urgency": {"type": "string", "enum": ["baixa", "media", "alta"]},
    },
    "required": ["symbol", "action", "conviction", "rationale", "urgency"],
    "additionalProperties": False,
}

PROPOSER_SYSTEM = (
    "Você é o analista de um agente de investimento pessoal em cripto "
    "(spot, Binance, perfil moderado). Você PROPÕE; um motor de regras "
    "determinístico decide — você nunca executa, nunca vê chaves e nunca "
    "calcula tamanho de posição. Receberá um contexto JSON (posições, "
    "equity, indicadores, notícias enriquecidas, macro) e uma lista de "
    "candidatos triados. Proponha UMA ação sobre UM dos candidatos: "
    "buy/sell/close com convicção 0-1 e racional curto em português, ou "
    "hold se nada justificar operar. O default é manter (hold) — "
    "overtrading é o erro número 1; só proponha operação com sinal claro. "
    "Nunca proponha símbolo fora dos candidatos."
)


def _hold(context: dict, rationale: str) -> Proposal:
    return Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale=rationale, cycle_id=context["cycle_id"])


def propose(client: LlmClient, context: dict,
            candidates: list[str]) -> tuple[Proposal, float]:
    if not candidates:
        return _hold(context, "sem candidatos na triagem"), 0.0
    payload = dict(context)
    payload["candidatos"] = candidates
    data, cost = client.structured(PROPOSER_MODEL, PROPOSER_SYSTEM, payload,
                                   PROPOSAL_SCHEMA, max_tokens=4000)
    if not data:
        return _hold(context,
                     "LLM recusou ou resposta inválida — ciclo sem ação"), cost
    action = Action(data["action"])
    if action is not Action.HOLD and data["symbol"] not in candidates:
        return _hold(context,
                     f"símbolo {data['symbol']} fora dos candidatos"), cost
    conviction = min(1.0, max(0.0, float(data["conviction"])))
    return Proposal(symbol=data["symbol"], action=action,
                    conviction=conviction, rationale=data["rationale"],
                    cycle_id=context["cycle_id"]), cost


def make_llm_proposer(client: LlmClient, store,
                      now_provider: Callable[[], datetime]):
    """Proposer (2a) real: triagem → proposta, com custo somado no store."""

    def proposer(context: dict) -> Proposal:
        total = 0.0
        try:
            candidates, cost = triage_candidates(client, context)
            total += cost
            proposal, cost = propose(client, context, candidates)
            total += cost
            return proposal
        except LlmError as err:
            return _hold(context, f"falha de LLM: {err}")
        finally:
            if total > 0:
                store.add_api_cost(now_provider().date(), total)

    return proposer
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_brain_proposer.py -v`
Expected: PASS (8 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/brain/proposer.py tests/test_brain_proposer.py
git commit -m "feat: proposer Sonnet com fallback HOLD e custo registrado"
```

---

### Task 5: Enriquecimento de notícias em batch (Haiku)

**Files:**
- Create: `src/invest_agent/brain/enrich.py`
- Test: `tests/test_brain_enrich.py`

**Interfaces:**
- Consumes: `LlmClient` (T1), `SqliteStore.unenriched_news`/`set_news_enrichment`/`add_api_cost` (T2/2a).
- Produces: `ENRICH_MODEL = "claude-haiku-4-5"`, `ENRICH_SCHEMA` (object `items`: array de `{id: string, summary: string, sentiment: enum ["positivo","negativo","neutro"], materiality: integer enum [1,2,3,4,5]}`), `enrich_news(client, store, now, limit: int = 20) -> dict` (contadores: `pending`, `enriched`, `cost_usd`) — UMA chamada com todas as pendentes; só aplica itens cujo `id` estava na lista pendente; materiality clampada em código para 1..5; refusal/parse → enriched 0 (tenta de novo no próximo ciclo); custo gravado. Task 6 consome (chama no ciclo com `--llm`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_enrich.py
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from invest_agent.brain.client import LlmClient
from invest_agent.brain.enrich import ENRICH_SCHEMA, enrich_news
from invest_agent.news.models import make_news_item
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _insert(store, title, url):
    item = make_news_item("src", title, url, NOW, NOW, "resumo bruto")
    store.insert_news([(item, ("BTCUSDT",))])
    return item


def _client(payload, stop_reason="end_turn"):
    def create_fn(**kwargs):
        usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                                cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if stop_reason == "refusal":
            return SimpleNamespace(content=[], stop_reason="refusal",
                                   usage=usage)
        block = SimpleNamespace(type="text", text=json.dumps(payload))
        return SimpleNamespace(content=[block], stop_reason="end_turn",
                               usage=usage)
    return LlmClient(create_fn=create_fn)


def test_enrich_aplica_e_custa(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    a = _insert(store, "Bitcoin sobe", "https://x.com/1")
    b = _insert(store, "ETF aprovado", "https://x.com/2")
    payload = {"items": [
        {"id": a.id, "summary": "alta forte", "sentiment": "positivo",
         "materiality": 4},
        {"id": "id-desconhecido", "summary": "x", "sentiment": "neutro",
         "materiality": 1},  # ignorado: não estava pendente
        {"id": b.id, "summary": "etf ok", "sentiment": "positivo",
         "materiality": 9},  # clampado para 5
    ]}
    stats = enrich_news(_client(payload), store, NOW)
    assert stats["pending"] == 2 and stats["enriched"] == 2
    assert stats["cost_usd"] > 0
    assert store.unenriched_news() == []
    recent = {t[0]: t for t in store.recent_news(NOW - timedelta(hours=1))}
    assert recent["ETF aprovado"][5] == 5  # materiality clampada
    assert store.api_cost_today(NOW.date()) > 0
    store.close()


def test_enrich_sem_pendentes_nao_chama(tmp_path):
    store = SqliteStore(tmp_path / "a.db")

    def explode(**kwargs):
        raise AssertionError("não devia chamar o LLM")

    stats = enrich_news(LlmClient(create_fn=explode), store, NOW)
    assert stats == {"pending": 0, "enriched": 0, "cost_usd": 0.0}
    store.close()


def test_enrich_refusal_mantem_pendentes(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    _insert(store, "t", "https://x.com/1")
    stats = enrich_news(_client({}, stop_reason="refusal"), store, NOW)
    assert stats["enriched"] == 0 and len(store.unenriched_news()) == 1
    store.close()


def test_schema_estrito():
    item = ENRICH_SCHEMA["properties"]["items"]["items"]
    assert item["properties"]["sentiment"]["enum"] == [
        "positivo", "negativo", "neutro"]
    assert item["additionalProperties"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_brain_enrich.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/brain/enrich.py
"""Enriquecimento de notícias (spec §4.1): resumo, sentimento e
materialidade 1-5, em UMA chamada Haiku por lote — barato e idempotente
(o que falhar continua pendente para o próximo ciclo). As notícias
entram como campos estruturados, nunca texto concatenado."""
from __future__ import annotations

from datetime import datetime

from ..storage.sqlite_store import SqliteStore
from .client import LlmClient

ENRICH_MODEL = "claude-haiku-4-5"

ENRICH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "summary": {"type": "string"},
                    "sentiment": {"type": "string",
                                  "enum": ["positivo", "negativo", "neutro"]},
                    "materiality": {"type": "integer",
                                    "enum": [1, 2, 3, 4, 5]},
                },
                "required": ["id", "summary", "sentiment", "materiality"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

ENRICH_SYSTEM = (
    "Você enriquece notícias para um agente de investimento em cripto. "
    "Para CADA item recebido (id, título, resumo bruto), devolva: resumo "
    "de 1 frase em português, sentimento para o mercado cripto "
    "(positivo/negativo/neutro) e materialidade 1-5 (5 = move mercado "
    "hoje; 1 = irrelevante). Devolva exatamente um item por id recebido."
)


def enrich_news(client: LlmClient, store: SqliteStore, now: datetime,
                limit: int = 20) -> dict:
    pending = store.unenriched_news(limit=limit)
    if not pending:
        return {"pending": 0, "enriched": 0, "cost_usd": 0.0}
    payload = {"noticias": [
        {"id": news_id, "titulo": title, "resumo_bruto": summary}
        for news_id, title, summary in pending
    ]}
    data, cost = client.structured(ENRICH_MODEL, ENRICH_SYSTEM, payload,
                                   ENRICH_SCHEMA, max_tokens=4000)
    if cost > 0:
        store.add_api_cost(now.date(), cost)
    if not data:
        return {"pending": len(pending), "enriched": 0, "cost_usd": cost}
    valid_ids = {p[0] for p in pending}
    enriched = 0
    for item in data.get("items", []):
        if item.get("id") not in valid_ids:
            continue
        materiality = min(5, max(1, int(item["materiality"])))
        store.set_news_enrichment(item["id"], item["summary"],
                                  item["sentiment"], materiality)
        enriched += 1
    return {"pending": len(pending), "enriched": enriched, "cost_usd": cost}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_brain_enrich.py -v`
Expected: PASS (4 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/brain/enrich.py tests/test_brain_enrich.py
git commit -m "feat: enriquecimento de notícias em lote (Haiku, idempotente)"
```

---

### Task 6: Integração no ciclo (`--llm`) + notícias/macro no contexto

**Files:**
- Modify: `src/invest_agent/orchestrator/cycle.py`
- Test: modificar `tests/test_cycle.py` (adicionar testes; NÃO alterar os existentes)
- Modify: `README.md`

**Interfaces:**
- `run_cycle(...)` ganha parâmetro opcional `news: list[tuple] = ()` e `macro: dict[str, float] | None = None` repassados a `build_context` (default mantém o comportamento atual — testes existentes intactos). Alternativa equivalente aceita: buscar news/macro DENTRO de run_cycle a partir do store; escolha a de menor diff mantendo os testes existentes verdes sem alteração.
- `main()` ganha `--llm`: exige `settings.anthropic_api_key` (erro claro em português se vazia); monta `LlmClient` real, roda `enrich_news` ANTES do proposer, monta `make_llm_proposer(client, store, lambda: now)`; busca `news = store.recent_news(now - timedelta(hours=24))` e `macro = {s: p.value for s in ("fng","selic","cambio") if (p := store.latest_macro(s))}` e passa ao ciclo. Sem `--llm`, comportamento atual (hold_proposer, news/macro vazios).
- `build_context` (2a) já aceita `news`/`macro` — as tuplas de `recent_news` têm 6 campos; adapte no chamador para o formato de 4 campos que `build_context` espera `(title, source, assets, published_at)` OU estenda `build_context` para aceitar os 6 e incluir sentiment/materiality no dict (preferível: estender, mantendo compatível com listas de 4 via tratamento no chamador — escolha UMA abordagem e documente).

- [ ] **Step 1: Write the failing tests (acrescentar a tests/test_cycle.py)**

```python
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
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `python3 -m pytest tests/test_cycle.py -v`
Expected: os 2 novos FALHAM; os 8 existentes PASSAM.

- [ ] **Step 3: Implementar**

Em `cycle.py`: assinatura `run_cycle(..., news: list[tuple] = (), macro: dict | None = None)`; no `build_context`, converta cada tupla de news para o formato aceito — se `build_context` da 2a espera 4 campos, adapte lá para aceitar 4 OU 6 campos: recomendação — em `snapshot.py`, mude o loop de news para:

```python
        "news": [
            {"title": n[0], "source": n[1], "assets": sorted(n[2]),
             "published_at": n[3],
             **({"sentiment": n[4], "materiality": n[5]} if len(n) > 4 else {})}
            for n in news
        ],
```

(retrocompatível com tuplas de 4 — o teste antigo de snapshot continua passando).

Em `main()`:

```python
    parser.add_argument("--llm", action="store_true",
                        help="usa o cérebro Claude (triagem + proposta)")
    ...
    news: list[tuple] = []
    macro: dict[str, float] = {}
    proposer = hold_proposer
    if args.llm:
        if not settings.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY ausente — necessário para --llm")
        from ..brain.client import LlmClient
        from ..brain.enrich import enrich_news
        from ..brain.proposer import make_llm_proposer
        client = LlmClient(api_key=settings.anthropic_api_key)
        enrich_news(client, store, now)
        proposer = make_llm_proposer(client, store, lambda: now)
        news = store.recent_news(now - timedelta(hours=24))
        macro = {name: point.value
                 for name in ("fng", "selic", "cambio")
                 if (point := store.latest_macro(name)) is not None}
    result = run_cycle(store, candle_store, adapter, engine, proposer,
                       settings, now, dry_run=args.dry_run,
                       news=news, macro=macro)
```

No `README.md`, na seção do ciclo, acrescentar: com `--llm` (e `ANTHROPIC_API_KEY`), o ciclo roda o cérebro completo — enriquecimento de notícias, triagem Haiku e proposta Sonnet — antes do motor de regras; sem a flag, proposer HOLD (dry-run operacional).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cycle.py tests/test_snapshot.py -v` e depois `python3 -m pytest`
Expected: tudo verde (os testes antigos de cycle/snapshot INTACTOS).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/orchestrator/ tests/test_cycle.py README.md
git commit -m "feat: ciclo com cérebro Claude opcional (--llm) e news/macro no contexto"
```

---

### Task 7: Revisão semanal Opus via Batch API → learnings/

**Files:**
- Create: `src/invest_agent/brain/weekly.py`
- Test: `tests/test_brain_weekly.py`
- Modify: `.gitignore` — NÃO adicionar `learnings/` (a spec §4.2 manda versionar os aprendizados em git; garanta que NÃO está ignorado)

**Interfaces:**
- Consumes: `SqliteStore.read_decisions` (1c), `usage_cost_usd` (T1).
- Produces: `WEEKLY_MODEL = "claude-opus-5"`; `build_weekly_prompt(decisions: list[DecisionRecord], since: datetime) -> str` (resumo em texto das decisões da semana: por dia, proposta→veredito→motivos; SEM dados sensíveis); `submit_weekly_review(batches, store, now) -> str` (monta 1 request no Batch API — `batches.create(requests=[{custom_id: "weekly-<data>", params: {...}}])` com o prompt e retorna o batch id; sem decisões na janela → retorna "" sem submeter); `collect_weekly_review(batches, batch_id: str, learnings_dir: Path, now) -> Path | None` (se `processing_status == "ended"`, extrai o texto do primeiro resultado succeeded, grava `learnings/<YYYY-MM-DD>-revisao-semanal.md` e retorna o Path; senão None); `main(argv)` com subcomandos `--submit` e `--collect BATCH_ID` (borda: cliente anthropic real, env). `batches` é o objeto injetável (fake nos testes) com `.create(...)` e `.retrieve(id)`/`.results(id)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brain_weekly.py
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from invest_agent.brain.weekly import (
    WEEKLY_MODEL, build_weekly_prompt, collect_weekly_review,
    submit_weekly_review,
)
from invest_agent.storage.sqlite_store import DecisionRecord, SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _decision(i, ts):
    return DecisionRecord(
        decision_id=f"d{i}", ts=ts, inputs_hash="h",
        snapshot_json="{}",
        proposal_json='{"symbol": "BTCUSDT", "action": "buy", "conviction": 0.3, "rationale": "sinal", "cycle_id": "c"}',
        verdict_json='{"status": "rejected", "reasons": ["cooldown ativo"]}')


def test_build_weekly_prompt_resume_decisoes():
    decisions = [_decision(1, NOW - timedelta(days=2)),
                 _decision(2, NOW - timedelta(days=1))]
    prompt = build_weekly_prompt(decisions, since=NOW - timedelta(days=7))
    assert "BTCUSDT" in prompt and "rejected" in prompt
    assert "cooldown ativo" in prompt


def test_submit_weekly_review(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_decision(1, NOW - timedelta(days=1)))
    created = {}

    class FakeBatches:
        def create(self, requests):
            created["requests"] = requests
            return SimpleNamespace(id="batch_123")

    batch_id = submit_weekly_review(FakeBatches(), store, NOW)
    assert batch_id == "batch_123"
    (req,) = created["requests"]
    assert req["custom_id"].startswith("weekly-")
    assert req["params"]["model"] == WEEKLY_MODEL
    store.close()


def test_submit_sem_decisoes_nao_submete(tmp_path):
    store = SqliteStore(tmp_path / "a.db")

    class Explode:
        def create(self, requests):
            raise AssertionError("não devia submeter")

    assert submit_weekly_review(Explode(), store, NOW) == ""
    store.close()


def test_collect_grava_learning(tmp_path):
    class FakeBatches:
        def retrieve(self, batch_id):
            return SimpleNamespace(processing_status="ended")

        def results(self, batch_id):
            block = SimpleNamespace(type="text", text="## Crítica\nOpere menos.")
            msg = SimpleNamespace(content=[block])
            yield SimpleNamespace(
                custom_id="weekly-2026-09-10",
                result=SimpleNamespace(type="succeeded",
                                       message=msg))

    path = collect_weekly_review(FakeBatches(), "batch_123",
                                 tmp_path / "learnings", NOW)
    assert path is not None and path.exists()
    assert "Opere menos" in path.read_text()
    assert path.name == "2026-09-10-revisao-semanal.md"


def test_collect_ainda_processando_devolve_none(tmp_path):
    class FakeBatches:
        def retrieve(self, batch_id):
            return SimpleNamespace(processing_status="in_progress")

    assert collect_weekly_review(FakeBatches(), "b", tmp_path, NOW) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_brain_weekly.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/brain/weekly.py
"""Revisão semanal (spec §4.3): Opus 5 via Batch API (−50%) lê o log de
decisões da semana e escreve uma crítica em learnings/ (markdown + git —
o agente lê no início do ciclo em fases futuras). Fluxo em 2 passos por
design do Batch API: --submit (cron domingo) e --collect (cron segunda)."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..storage.sqlite_store import SqliteStore

WEEKLY_MODEL = "claude-opus-5"

WEEKLY_SYSTEM = (
    "Você é o revisor semanal de um agente de investimento em cripto. "
    "Analise o log de decisões da semana (propostas do LLM e vereditos do "
    "motor de regras) e escreva uma crítica construtiva em markdown, em "
    "português: padrões de erro (overtrading? convicção mal calibrada? "
    "propostas rejeitadas repetidamente pelo mesmo motivo?), o que manter, "
    "e no máximo 3 recomendações concretas para a semana seguinte."
)


def build_weekly_prompt(decisions: list, since: datetime) -> str:
    lines = [f"Decisões desde {since:%Y-%m-%d}:"]
    for record in decisions:
        proposal = json.loads(record.proposal_json)
        verdict = json.loads(record.verdict_json)
        reasons = "; ".join(verdict.get("reasons", []))
        lines.append(
            f"- {record.ts:%Y-%m-%d %H:%M} {proposal.get('symbol')} "
            f"{proposal.get('action')} conv={proposal.get('conviction')} → "
            f"{verdict.get('status')}" + (f" ({reasons})" if reasons else ""))
    return "\n".join(lines)


def submit_weekly_review(batches, store: SqliteStore, now: datetime) -> str:
    since = now - timedelta(days=7)
    decisions = [d for d in store.read_decisions() if d.ts >= since]
    if not decisions:
        return ""
    prompt = build_weekly_prompt(decisions, since)
    batch = batches.create(requests=[{
        "custom_id": f"weekly-{now:%Y-%m-%d}",
        "params": {
            "model": WEEKLY_MODEL,
            "max_tokens": 8000,
            "system": WEEKLY_SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        },
    }])
    return batch.id


def collect_weekly_review(batches, batch_id: str, learnings_dir: Path,
                          now: datetime) -> Path | None:
    if batches.retrieve(batch_id).processing_status != "ended":
        return None
    for result in batches.results(batch_id):
        if result.result.type != "succeeded":
            continue
        text = next((b.text for b in result.result.message.content
                     if getattr(b, "type", "") == "text"), "")
        learnings_dir.mkdir(parents=True, exist_ok=True)
        path = learnings_dir / f"{now:%Y-%m-%d}-revisao-semanal.md"
        path.write_text(text, encoding="utf-8")
        return path
    return None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Revisão semanal (Opus)")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--collect", metavar="BATCH_ID")
    parser.add_argument("--db", default=Path("data/agent.db"), type=Path)
    parser.add_argument("--learnings", default=Path("learnings"), type=Path)
    args = parser.parse_args(argv)

    import anthropic

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    batches = client.messages.batches
    now = datetime.now(timezone.utc)  # borda de composição
    if args.submit:
        store = SqliteStore(args.db)
        batch_id = submit_weekly_review(batches, store, now)
        store.close()
        print(f"batch submetido: {batch_id}" if batch_id
              else "sem decisões na semana — nada a revisar")
    elif args.collect:
        path = collect_weekly_review(batches, args.collect, args.learnings, now)
        print(f"learning gravado: {path}" if path
              else "batch ainda processando — tente mais tarde")
    else:
        parser.error("use --submit ou --collect BATCH_ID")


if __name__ == "__main__":
    main()
```

Confirme que `.gitignore` NÃO ignora `learnings/` (a spec manda versionar).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_brain_weekly.py -v` e `python3 -m pytest`
Expected: tudo verde.

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/brain/weekly.py tests/test_brain_weekly.py
git commit -m "feat: revisão semanal Opus via Batch API escrevendo em learnings/"
```

---

## Self-review do plano (executada na escrita)

- **Cobertura da spec (escopo 2b):** §4.3 passo 2 (Haiku ≤3 candidatos) → T3; passo 3 (Sonnet, schema strict, caching) → T4+T1; passo 4 (refusal/schema inválido = ciclo sem ação) → T1/T3/T4 (fallback HOLD); enriquecimento batch §4.1 → T5+T2; revisão semanal Opus/Batch/learnings §4.3+§4.2 → T7; custo por chamada no decision-log/breaker → T1/T4/T5 (`add_api_cost`); estado read-only p/ LLM §3 → constraint global (contexto dict pronto).
- **Placeholders:** nenhum; todo step tem código completo.
- **Consistência de tipos:** `LlmClient.structured → (dict|None, float)` usado em T3/T4/T5; `Proposer = Callable[[dict], Proposal]` (2a) satisfeito por `make_llm_proposer`; tuplas de `recent_news` (6 campos) tratadas no snapshot com retrocompatibilidade de 4 (T6); `DecisionRecord.ts` é datetime (comparação `d.ts >= since` em T7 ok); `store.add_api_cost(day: date, usd)` chamado com `.date()` em T4/T5.
- **Aritmética conferida:** custo haiku 1000in/500out = 0.0035; custo com cache = (3000 + 7500 + 600 + 6000)/1e6 = 0.0171; conviction 1.7 → clamp 1.0; conviction 0.019 → BUY aprovado no ciclo (equity 10k, alvo 19 < 200).
- **Risco conhecido (registrado):** a chamada real usa `output_config` — se a versão instalada do SDK `anthropic` não tipar o parâmetro, ele ainda passa (SDK encaminha kwargs desconhecidos? Python SDK aceita `output_config` nas versões atuais; se a instalação local for antiga demais e rejeitar, o implementer reporta NEEDS_CONTEXT com a versão instalada — NÃO troque para `extra_body` sem registrar).
