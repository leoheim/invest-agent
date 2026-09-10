# Fase 2c — Telegram + HITL Duro + Operação — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar a Fase 2 da spec: canal Telegram bidirecional (push de eventos, comandos, botões de aprovação com TTL), os dois HITL obrigatórios que estavam deferidos (primeiro trade em ativo novo; primeira ordem pós-circuit-breaker), execução de ordens aprovadas, whitelist semanal persistida e o runbook de operação (VPS, cron, systemd, testnet→live).

**Architecture:** `src/invest_agent/telegram/` (cliente stdlib da Bot API com transport injetável, formatadores pt-BR, bot long-poll com autorização por chat_id único), `src/invest_agent/orchestrator/hitl.py` (downgrades APPROVED→NEEDS_APPROVAL baseados em histórico), integração no ciclo (executar aprovados, notificar resultados/notícias materiais), `src/invest_agent/jobs/whitelist.py` (job semanal), `docs/ops.md` (runbook). Nenhuma dep nova (Bot API é REST simples — ruling).

**Tech Stack:** stdlib para Telegram (urllib/json); deps de runtime ficam as 4 existentes.

**Spec:** `docs/superpowers/specs/2026-09-05-invest-agent-design.md` (§4.4 HITL, §4.6 Telegram, §5 fases). **Rulings do controller (no ledger):**
1. **Telegram via stdlib** (sem python-telegram-bot): a Bot API é REST trivial (3 métodos usados); o padrão transport-injetável do projeto dá testabilidade total. A spec lista a lib como ecossistema, não mandato (mesmo ruling do feedparser na 1c).
2. **HITL duro como override do orquestrador** (não no engine): "primeiro trade" e "pós-breaker" precisam de HISTÓRICO (decision_log/halt_state) que o engine puro da Fase 0 não vê por design. `apply_hitl_overrides` roda DEPOIS do veredito e só endurece (APPROVED→NEEDS_APPROVAL), nunca afrouxa.
3. **Aprovação executa no próprio bot?** Não — o bot só marca `approved`; a EXECUÇÃO acontece no início do ciclo seguinte (`_execute_approved`), com o adapter e a reconciliação do ciclo. Custo: latência de até 1h entre aprovar e executar (documentado no runbook: rode um ciclo manual após aprovar se tiver pressa).
4. **Chat livre respondido pelo agente (spec §4.6)** fica de fora do escopo 2c: exigiria o LLM no bot com estado read-only — anotado no runbook como extensão futura; os comandos cobrem a operação.

## Global Constraints

- Zero deps novas; Telegram via urllib com `transport: Callable[[str, bytes | None], bytes]` injetável (POST quando body≠None); NENHUM teste acessa a rede.
- **Só o chat_id configurado é atendido** — updates de qualquer outro chat são ignorados silenciosamente (single user, spec §4.6).
- Token do Telegram nunca em logs/erros (ele aparece na URL — mensagens de erro NÃO podem incluir a URL).
- HITL: aprovação via botões inline OU `/aprovar <id>`; expiração TTL 10 min continua valendo (quem expira é o ciclo — já implementado na 2a).
- Mensagens pt-BR; datas UTC; `now` parâmetro; bordas únicas: `main()` de cada CLI; commits pequenos, sem assinatura.

---

### Task 1: Cliente Telegram stdlib

**Files:**
- Create: `src/invest_agent/telegram/__init__.py`
- Create: `src/invest_agent/telegram/client.py`
- Test: `tests/test_telegram_client.py`

**Interfaces:**
- Produces: `TelegramError(Exception)`; classe `TelegramClient(token: str, chat_id: str, transport: Callable[[str, bytes | None], bytes] | None = None)` com:
  - `send_message(text: str, buttons: list[tuple[str, str]] | None = None) -> None` — POST `sendMessage` com `{chat_id, text}`; `buttons` vira `reply_markup.inline_keyboard` (uma linha, cada tupla = (texto, callback_data)).
  - `get_updates(offset: int) -> list[dict]` — GET `getUpdates?offset=<offset>&timeout=25`; retorna APENAS updates do `chat_id` configurado (mensagens: `update["message"]["chat"]["id"]`; callbacks: `update["callback_query"]["message"]["chat"]["id"]`); updates de outros chats são consumidos (avançam offset) mas não retornados.
  - `answer_callback(callback_id: str) -> None` — POST `answerCallbackQuery`.
  - Resposta com `ok: false` ou erro de transporte → `TelegramError` SEM a URL na mensagem (o token está nela). Tasks 2, 5, 6 consomem.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_telegram_client.py
import json

import pytest

from invest_agent.telegram.client import TelegramClient, TelegramError

TOKEN, CHAT = "tok-SECRETO", "42"


def _client(responses):
    calls = []

    def transport(url, body):
        calls.append((url, body))
        payload = responses.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload).encode()

    client = TelegramClient(TOKEN, CHAT, transport=transport)
    return client, calls


def test_send_message_simples():
    client, calls = _client([{"ok": True}])
    client.send_message("olá")
    url, body = calls[0]
    assert url.endswith(f"/bot{TOKEN}/sendMessage")
    data = json.loads(body)
    assert data == {"chat_id": CHAT, "text": "olá"}


def test_send_message_com_botoes():
    client, calls = _client([{"ok": True}])
    client.send_message("aprovar?", buttons=[("Aprovar", "ap:d1"),
                                             ("Rejeitar", "rj:d1")])
    data = json.loads(calls[0][1])
    teclado = data["reply_markup"]["inline_keyboard"]
    assert teclado == [[{"text": "Aprovar", "callback_data": "ap:d1"},
                        {"text": "Rejeitar", "callback_data": "rj:d1"}]]


def test_get_updates_filtra_chat_autorizado():
    updates = {"ok": True, "result": [
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/status"}},
        {"update_id": 2, "message": {"chat": {"id": 999}, "text": "/kill"}},
        {"update_id": 3, "callback_query": {"id": "cb1", "data": "ap:d1",
                                            "message": {"chat": {"id": 42}}}},
        {"update_id": 4, "callback_query": {"id": "cb2", "data": "ap:d2",
                                            "message": {"chat": {"id": 999}}}},
    ]}
    client, calls = _client([updates])
    out = client.get_updates(offset=7)
    assert [u["update_id"] for u in out] == [1, 3]  # 999 ignorado
    assert "offset=7" in calls[0][0] and "timeout=25" in calls[0][0]
    assert calls[0][1] is None  # GET


def test_answer_callback():
    client, calls = _client([{"ok": True}])
    client.answer_callback("cb1")
    assert calls[0][0].endswith("/answerCallbackQuery")
    assert json.loads(calls[0][1]) == {"callback_query_id": "cb1"}


def test_erro_nao_vaza_token():
    client, _ = _client([{"ok": False, "description": "bad request"}])
    with pytest.raises(TelegramError) as err:
        client.send_message("x")
    assert TOKEN not in str(err.value)


def test_erro_de_transporte_nao_vaza_token():
    client, _ = _client([OSError(f"http://api.telegram.org/bot{TOKEN}/x")])
    with pytest.raises(TelegramError) as err:
        client.send_message("x")
    assert TOKEN not in str(err.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_telegram_client.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

`src/invest_agent/telegram/__init__.py`: vazio.

```python
# src/invest_agent/telegram/client.py
"""Cliente mínimo da Bot API do Telegram em stdlib (spec §4.6). Um único
chat autorizado: updates de qualquer outro chat são descartados. O token
vive na URL — nenhuma mensagem de erro pode conter a URL."""
from __future__ import annotations

import json
import urllib.request
from typing import Callable

API_BASE = "https://api.telegram.org"


class TelegramError(Exception):
    """Falha na Bot API (mensagem NUNCA contém token/URL)."""


def _default_transport(url: str, body: bytes | None) -> bytes:
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"},
            method="POST")
    with urllib.request.urlopen(req, timeout=35) as resp:
        return resp.read()


class TelegramClient:
    def __init__(self, token: str, chat_id: str,
                 transport: Callable[[str, bytes | None], bytes] | None = None):
        self._token = token
        self.chat_id = chat_id
        self._transport = transport or _default_transport

    def _call(self, method: str, payload: dict | None,
              query: str = "") -> dict:
        url = f"{API_BASE}/bot{self._token}/{method}"
        if query:
            url += f"?{query}"
        body = (json.dumps(payload).encode() if payload is not None else None)
        try:
            data = json.loads(self._transport(url, body))
        except Exception as err:
            raise TelegramError(
                f"falha de transporte no método {method}: "
                f"{type(err).__name__}") from err
        if not data.get("ok"):
            raise TelegramError(
                f"Bot API recusou {method}: {data.get('description', '?')}")
        return data

    def send_message(self, text: str,
                     buttons: list[tuple[str, str]] | None = None) -> None:
        payload: dict = {"chat_id": self.chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [[
                {"text": label, "callback_data": data}
                for label, data in buttons
            ]]}
        self._call("sendMessage", payload)

    def get_updates(self, offset: int) -> list[dict]:
        data = self._call("getUpdates", None,
                          query=f"offset={offset}&timeout=25")
        out = []
        for update in data.get("result", []):
            message = update.get("message") or {}
            callback = update.get("callback_query") or {}
            chat = (message.get("chat")
                    or (callback.get("message") or {}).get("chat") or {})
            if str(chat.get("id")) == str(self.chat_id):
                out.append(update)
        return out

    def answer_callback(self, callback_id: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_telegram_client.py -v`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/telegram/ tests/test_telegram_client.py
git commit -m "feat: cliente Telegram stdlib com chat único e botões inline"
```

---

### Task 2: Formatadores de mensagens (pt-BR)

**Files:**
- Create: `src/invest_agent/telegram/format.py`
- Test: `tests/test_telegram_format.py`

**Interfaces:**
- Consumes: `CycleResult` (2a), `Verdict`/`OrderIntent` (Fase 0), `MODERADO` (Fase 0), `SqliteStore` (leitura).
- Produces (todas retornam `str` pt-BR, sem markdown especial):
  - `format_cycle_result(result: CycleResult, order: OrderIntent | None) -> str` — "✅ ordem executada …" / "🚫 rejeitado: motivo1; motivo2" / "⏳ aguardando aprovação" / "⛔ halt …" / "· hold".
  - `format_hitl_request(decision_id: str, order: OrderIntent, reasons: list[str]) -> str` — texto do pedido de aprovação (símbolo, lado, qty, preço, motivo) — os botões são responsabilidade do chamador.
  - `format_breaker(level_name: str) -> str`.
  - `format_material_news(title: str, source: str, symbol: str, materiality: int) -> str`.
  - `build_status_text(store: SqliteStore, profile, now: datetime) -> str` — equity marks? Não: posições da tabela + halt ativo + custo de API do dia + pendências HITL abertas + nome do perfil (equity ao vivo exige adapter; o status usa o que o store sabe — documentado na docstring).
  Tasks 5 e 6 consomem.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_telegram_format.py
from datetime import datetime, timedelta, timezone

from invest_agent.config import MODERADO
from invest_agent.models import OrderIntent
from invest_agent.orchestrator.cycle import CycleResult
from invest_agent.storage.sqlite_store import SqliteStore
from invest_agent.telegram.format import (
    build_status_text, format_breaker, format_cycle_result,
    format_hitl_request, format_material_news,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
ORDER = OrderIntent(symbol="BTCUSDT", side="BUY", qty=0.001,
                    limit_price=100_000.0, stop_loss_price=95_000.0,
                    client_order_id="ia-abc")


def test_format_cycle_result_executada():
    result = CycleResult("2026091012", "approved", [], True)
    texto = format_cycle_result(result, ORDER)
    assert "BTCUSDT" in texto and "BUY" in texto and "executada" in texto


def test_format_cycle_result_rejeitada_lista_motivos():
    result = CycleResult("2026091012", "rejected",
                         ["cooldown ativo", "spread alto"], False)
    texto = format_cycle_result(result, None)
    assert "cooldown ativo" in texto and "spread alto" in texto


def test_format_cycle_result_hold_e_curto():
    result = CycleResult("2026091012", "approved", [], False)
    assert len(format_cycle_result(result, None)) < 80


def test_format_hitl_request():
    texto = format_hitl_request("d1", ORDER, ["acima do limiar"])
    assert "d1" in texto and "BTCUSDT" in texto and "acima do limiar" in texto


def test_format_breaker_e_news():
    assert "DAY" in format_breaker("DAY")
    news = format_material_news("ETF aprovado", "coindesk", "BTCUSDT", 5)
    assert "ETF aprovado" in news and "5" in news


def test_build_status_text(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.upsert_position("BTCUSDT", 0.5, 90_000.0)
    store.add_api_cost(NOW.date(), 0.42)
    store.add_pending("d1", NOW, NOW + timedelta(minutes=10))
    texto = build_status_text(store, MODERADO, NOW)
    assert "BTCUSDT" in texto and "0.5" in texto
    assert "0.42" in texto and "moderado" in texto
    assert "d1" in texto
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_telegram_format.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/telegram/format.py
"""Mensagens do agente para o dono (spec §4.6), em português simples.
build_status_text usa só o que o store sabe (posições registradas, halt,
custo de API, pendências) — equity ao vivo exigiria o adapter e fica com
o ciclo."""
from __future__ import annotations

from datetime import datetime

from ..models import OrderIntent
from ..orchestrator.cycle import CycleResult
from ..orchestrator.state import active_halt
from ..breakers import HaltLevel
from ..storage.sqlite_store import SqliteStore


def format_cycle_result(result: CycleResult,
                        order: OrderIntent | None) -> str:
    if result.verdict_status == "halted":
        return f"⛔ ciclo {result.cycle_id}: halt — {'; '.join(result.reasons)}"
    if result.verdict_status == "rejected":
        return (f"🚫 ciclo {result.cycle_id}: proposta rejeitada — "
                f"{'; '.join(result.reasons)}")
    if result.verdict_status == "needs_approval":
        return f"⏳ ciclo {result.cycle_id}: ordem aguardando sua aprovação"
    if result.executed and order is not None:
        return (f"✅ ciclo {result.cycle_id}: ordem executada — "
                f"{order.side} {order.qty:g} {order.symbol} @ "
                f"{order.limit_price:g}"
                + (f" (stop {order.stop_loss_price:g})"
                   if order.stop_loss_price else ""))
    return f"· ciclo {result.cycle_id}: sem ação (hold)"


def format_hitl_request(decision_id: str, order: OrderIntent,
                        reasons: list[str]) -> str:
    return (f"⏳ Aprovação necessária [{decision_id}]\n"
            f"{order.side} {order.qty:g} {order.symbol} @ "
            f"{order.limit_price:g}\n"
            f"Motivo: {'; '.join(reasons)}\n"
            f"Expira em 10 minutos. Use os botões ou "
            f"/aprovar {decision_id} · /rejeitar {decision_id}")


def format_breaker(level_name: str) -> str:
    return (f"⛔ Circuit breaker acionado (nível {level_name}). "
            "Novas ordens suspensas conforme o perfil de risco.")


def format_material_news(title: str, source: str, symbol: str,
                         materiality: int) -> str:
    return (f"📰 [{source}] materialidade {materiality} em {symbol}: "
            f"{title}")


def build_status_text(store: SqliteStore, profile, now: datetime) -> str:
    lines = [f"📊 Status — {now:%Y-%m-%d %H:%M} UTC",
             f"Perfil: {profile.name}"]
    positions = store.get_positions()
    if positions:
        lines.append("Posições registradas:")
        for symbol, (qty, avg_price, _) in sorted(positions.items()):
            lines.append(f"  {symbol}: {qty:g} @ {avg_price:g}")
    else:
        lines.append("Sem posições registradas.")
    halt = active_halt(store, now)
    lines.append(f"Halt: {halt.name}" if halt is not HaltLevel.NONE
                 else "Halt: nenhum")
    lines.append(f"Custo de API hoje: US$ {store.api_cost_today(now.date()):.2f}")
    pending = store.get_pending()
    if pending:
        ids = ", ".join(p[0] for p in pending)
        lines.append(f"Aprovações pendentes: {ids}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_telegram_format.py -v`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/telegram/format.py tests/test_telegram_format.py
git commit -m "feat: formatadores pt-BR das mensagens do Telegram"
```

---

### Task 3: Store — whitelist persistida + leitura de ordem aprovada

**Files:**
- Modify: `src/invest_agent/storage/sqlite_store.py`
- Test: `tests/test_sqlite_whitelist.py`

**Interfaces:**
- Produces (só adições): tabela `whitelist(symbol TEXT PRIMARY KEY, updated_at TEXT)` → `set_whitelist(symbols: Iterable[str], now: datetime)` (substitui o conjunto inteiro), `get_whitelist() -> frozenset[str]` (vazio se nunca setada); `approved_pending() -> list[str]` (decision_ids com status "approved"); `get_decision(decision_id: str) -> DecisionRecord | None`. Status novo no vocabulário de pending: "executed" (via `set_pending_status` existente). NADA existente muda. Tasks 5, 6, 7 consomem.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sqlite_whitelist.py
from datetime import datetime, timezone

from invest_agent.storage.sqlite_store import DecisionRecord, SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def test_whitelist_set_get_substitui(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    assert store.get_whitelist() == frozenset()
    store.set_whitelist(["BTCUSDT", "ETHUSDT"], NOW)
    assert store.get_whitelist() == frozenset({"BTCUSDT", "ETHUSDT"})
    store.set_whitelist(["SOLUSDT"], NOW)  # substitui, não acumula
    assert store.get_whitelist() == frozenset({"SOLUSDT"})
    store.close()


def test_approved_pending_e_get_decision(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record = DecisionRecord(decision_id="d1", ts=NOW, inputs_hash="h",
                            snapshot_json="{}", proposal_json="{}",
                            verdict_json="{}",
                            order_json='{"symbol": "BTCUSDT"}')
    store.append_decision(record)
    store.add_pending("d1", NOW, NOW)
    assert store.approved_pending() == []
    store.set_pending_status("d1", "approved")
    assert store.approved_pending() == ["d1"]
    lido = store.get_decision("d1")
    assert lido == record
    assert store.get_decision("nao-existe") is None
    store.set_pending_status("d1", "executed")
    assert store.approved_pending() == []
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sqlite_whitelist.py -v`
Expected: FAIL com `AttributeError`

- [ ] **Step 3: Write minimal implementation**

Schema: `CREATE TABLE IF NOT EXISTS whitelist (symbol TEXT PRIMARY KEY, updated_at TEXT NOT NULL);`

```python
    # --- whitelist persistida (job semanal, Fase 2c) ---

    def set_whitelist(self, symbols, now: datetime) -> None:
        self._con.execute("DELETE FROM whitelist")
        self._con.executemany(
            "INSERT INTO whitelist (symbol, updated_at) VALUES (?,?)",
            [(s, now.isoformat()) for s in symbols])
        self._con.commit()

    def get_whitelist(self) -> frozenset[str]:
        rows = self._con.execute("SELECT symbol FROM whitelist").fetchall()
        return frozenset(r[0] for r in rows)

    # --- aprovação HITL (Fase 2c) ---

    def approved_pending(self) -> list[str]:
        rows = self._con.execute(
            "SELECT decision_id FROM pending_approvals"
            " WHERE status='approved' ORDER BY created_at").fetchall()
        return [r[0] for r in rows]

    def get_decision(self, decision_id: str):
        row = self._con.execute(
            "SELECT decision_id, ts, inputs_hash, snapshot_json,"
            " proposal_json, verdict_json, order_json, fills_json,"
            " api_cost_usd FROM decision_log WHERE decision_id=?",
            (decision_id,)).fetchone()
        if row is None:
            return None
        return DecisionRecord(decision_id=row[0],
                              ts=datetime.fromisoformat(row[1]),
                              inputs_hash=row[2], snapshot_json=row[3],
                              proposal_json=row[4], verdict_json=row[5],
                              order_json=row[6], fills_json=row[7],
                              api_cost_usd=row[8])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_sqlite_whitelist.py tests/test_sqlite_store.py tests/test_sqlite_ops.py -v`
Expected: PASS (novos + antigos intactos).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/storage/sqlite_store.py tests/test_sqlite_whitelist.py
git commit -m "feat: whitelist persistida e leitura de aprovações no store"
```

---

### Task 4: HITL duro — primeiro trade e pós-breaker

**Files:**
- Create: `src/invest_agent/orchestrator/hitl.py`
- Test: `tests/test_hitl.py`

**Interfaces:**
- Consumes: `Verdict`/`VerdictStatus`/`Proposal`/`Action`/`PortfolioState` (Fase 0), `SqliteStore` (get_halt, last_order_at_by_symbol).
- Produces: `apply_hitl_overrides(verdict: Verdict, proposal: Proposal, portfolio: PortfolioState, store: SqliteStore, now: datetime) -> Verdict` — regras (spec §4.4 "Também obrigatório"):
  1. **Primeiro trade em ativo novo:** se `verdict.status == APPROVED`, `proposal.action == BUY`, o símbolo NÃO está em `portfolio.positions` E NUNCA teve ordem (`last_order_at_by_symbol()` sem o símbolo) → NEEDS_APPROVAL com motivo "primeiro trade em BTCUSDT — aprovação humana obrigatória".
  2. **Primeira ordem pós-circuit-breaker:** se `verdict.status == APPROVED` e existe halt registrado (`get_halt()` não-None, qualquer released/estado) cujo `set_at` é POSTERIOR à última ordem global (ou nunca houve ordem) → NEEDS_APPROVAL com motivo "primeira ordem após circuit breaker (nível X) — aprovação humana obrigatória". (Halt ativo nem chega aqui — o ciclo bloqueia antes; esta regra pega a RETOMADA.)
  - Nunca altera REJECTED/NEEDS_APPROVAL; nunca altera HOLD (order None). Ambas as regras podem acumular com o motivo existente? O veredito chega APPROVED sem motivos; o override retorna NEEDS_APPROVAL com a lista dos motivos das regras disparadas (pode disparar as duas). Task 5 integra no ciclo.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hitl.py
from datetime import datetime, timedelta, timezone

from invest_agent.breakers import HaltLevel
from invest_agent.models import (Action, OrderIntent, PortfolioState,
                                 Position, Proposal, Verdict, VerdictStatus)
from invest_agent.orchestrator.hitl import apply_hitl_overrides
from invest_agent.orchestrator.state import record_halt_if_needed
from invest_agent.storage.sqlite_store import DecisionRecord, SqliteStore

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
ORDER = OrderIntent(symbol="SOLUSDT", side="BUY", qty=1.0,
                    limit_price=100.0, stop_loss_price=95.0,
                    client_order_id="ia-x")
APPROVED = Verdict(VerdictStatus.APPROVED, [], ORDER)


def _proposal(action=Action.BUY, symbol="SOLUSDT"):
    return Proposal(symbol=symbol, action=action, conviction=0.1,
                    rationale="x", cycle_id="c")


def _order_decision(decision_id, ts, symbol="SOLUSDT"):
    return DecisionRecord(decision_id=decision_id, ts=ts, inputs_hash="h",
                          snapshot_json="{}", proposal_json="{}",
                          verdict_json="{}",
                          order_json=f'{{"symbol": "{symbol}"}}')


def test_primeiro_trade_exige_aprovacao(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.NEEDS_APPROVAL
    assert any("primeiro trade" in r for r in verdict.reasons)
    assert verdict.order == ORDER  # a ordem sobrevive p/ aprovação
    store.close()


def test_simbolo_ja_operado_nao_dispara(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_order_decision("d0", NOW - timedelta(days=3)))
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.APPROVED
    store.close()


def test_posicao_existente_nao_dispara(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    portfolio = PortfolioState(
        equity=10_000.0, cash=5_000.0,
        positions={"SOLUSDT": Position("SOLUSDT", 1.0, 90.0)})
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.APPROVED
    store.close()


def test_pos_breaker_exige_aprovacao(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    store.append_decision(_order_decision("d0", NOW - timedelta(days=2)))
    record_halt_if_needed(store, HaltLevel.DAY, NOW - timedelta(days=1))
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    # símbolo já operado (d0) → regra 1 não dispara; regra 2 sim
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.NEEDS_APPROVAL
    assert any("circuit breaker" in r for r in verdict.reasons)
    store.close()


def test_ordem_depois_do_halt_ja_passou_nao_dispara(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    record_halt_if_needed(store, HaltLevel.DAY, NOW - timedelta(days=2))
    store.append_decision(_order_decision("d1", NOW - timedelta(days=1)))
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    verdict = apply_hitl_overrides(APPROVED, _proposal(), portfolio,
                                   store, NOW)
    assert verdict.status is VerdictStatus.APPROVED
    store.close()


def test_rejected_e_hold_intocados(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    portfolio = PortfolioState(equity=10_000.0, cash=10_000.0)
    rejected = Verdict(VerdictStatus.REJECTED, ["motivo"], None)
    assert apply_hitl_overrides(rejected, _proposal(), portfolio,
                                store, NOW) is rejected
    hold_ok = Verdict(VerdictStatus.APPROVED, [], None)  # HOLD: sem ordem
    assert apply_hitl_overrides(hold_ok, _proposal(Action.HOLD), portfolio,
                                store, NOW) is hold_ok
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_hitl.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/orchestrator/hitl.py
"""HITL obrigatório da spec §4.4 que exige HISTÓRICO (por isso vive no
orquestrador, não no engine puro): primeiro trade em ativo novo e
primeira ordem após um circuit breaker. Só endurece vereditos
(APPROVED → NEEDS_APPROVAL) — nunca afrouxa."""
from __future__ import annotations

from datetime import datetime

from ..models import (Action, PortfolioState, Proposal, Verdict,
                      VerdictStatus)
from ..storage.sqlite_store import SqliteStore


def apply_hitl_overrides(verdict: Verdict, proposal: Proposal,
                         portfolio: PortfolioState, store: SqliteStore,
                         now: datetime) -> Verdict:
    if verdict.status is not VerdictStatus.APPROVED or verdict.order is None:
        return verdict

    reasons: list[str] = []
    last_orders = store.last_order_at_by_symbol()

    if (proposal.action is Action.BUY
            and proposal.symbol not in portfolio.positions
            and proposal.symbol not in last_orders):
        reasons.append(f"primeiro trade em {proposal.symbol} — "
                       "aprovação humana obrigatória")

    halt = store.get_halt()
    if halt is not None:
        level_name, set_at, _ = halt
        last_global = max(last_orders.values(), default=None)
        if last_global is None or last_global < set_at:
            reasons.append(f"primeira ordem após circuit breaker "
                           f"(nível {level_name}) — aprovação humana "
                           "obrigatória")

    if not reasons:
        return verdict
    return Verdict(VerdictStatus.NEEDS_APPROVAL, reasons, verdict.order)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_hitl.py -v`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/orchestrator/hitl.py tests/test_hitl.py
git commit -m "feat: HITL duro — primeiro trade e primeira ordem pós-breaker"
```

---

### Task 5: Ciclo — executar aprovados, HITL overrides e notificações

**Files:**
- Modify: `src/invest_agent/orchestrator/cycle.py`
- Test: acrescentar a `tests/test_cycle.py` (existentes INTACTOS)
- Modify: `README.md`

**Interfaces:**
- `run_cycle(...)` ganha `notifier: Callable[[str], None] | None = None` (silencioso quando None; erros do notifier são engolidos — notificação nunca derruba o ciclo) e passa a:
  1. No início (após expirar pendências): `_execute_approved(store, adapter, notifier, dry_run)` — para cada `approved_pending()`: carrega `get_decision(id)`, reconstrói `OrderIntent(**json.loads(order_json))`, executa via `_execute` existente, marca `set_pending_status(id, "executed")`, notifica "✅ ordem aprovada executada …". Em `dry_run` não executa nem marca.
  2. Depois do `engine.evaluate` + `record_halt_if_needed`: `verdict = apply_hitl_overrides(verdict, proposal, portfolio, store, now)` ANTES do `_record` (o decision_log guarda o veredito final).
  3. NEEDS_APPROVAL → além do `add_pending`, notifica `format_hitl_request` (o main passa um notifier com botões? Não: `run_cycle` recebe também `hitl_notifier: Callable[[str, OrderIntent, list[str]], None] | None = None` — o main o implementa com botões; se None, silencioso).
  4. Whitelist: `main()` passa a montar o engine com `store.get_whitelist() or ALWAYS_INCLUDED`.
  5. `main()`: se `settings.telegram_token` e `chat_id` setados, monta `TelegramClient` e passa `notifier=lambda texto: tg.send_message(texto)` e `hitl_notifier` com botões `[("Aprovar", f"ap:{id}"), ("Rejeitar", f"rj:{id}")]`... o id só existe dentro do run_cycle — assinatura: `hitl_notifier(decision_id, order, reasons)`; o main formata e envia com botões. Após o ciclo, main envia `format_cycle_result` e, com `--llm`, push de notícias com materialidade ≥4 cujo asset esteja nas posições (`format_material_news`, uma por notícia, no máximo 3).

- [ ] **Step 1: Write the failing tests (acrescentar a tests/test_cycle.py)**

```python
def test_hitl_primeiro_trade_downgrade_no_ciclo(tmp_path):
    # BUY pequeno (0.019 → 19 USDT < 2%) seria APPROVED; mas é o primeiro
    # trade do símbolo → NEEDS_APPROVAL via override
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    assert result.verdict_status == "needs_approval"
    assert any("primeiro trade" in r for r in result.reasons)
    assert adapter.orders == []
    assert len(store.get_pending()) == 1
    store.close()


def test_aprovado_executa_no_ciclo_seguinte(tmp_path):
    import json as _json
    prop = Proposal(symbol="BTCUSDT", action=Action.BUY, conviction=0.019,
                    rationale="x", cycle_id="2026091012")
    store, cs, adapter, engine, proposer, settings = _fixture(
        tmp_path, proposal=prop)
    run_cycle(store, cs, adapter, engine, proposer, settings, NOW)
    (pend,) = store.get_pending()
    store.set_pending_status(pend[0], "approved")  # dono aprovou no bot
    avisos = []
    hold = Proposal(symbol="BTCUSDT", action=Action.HOLD, conviction=0.0,
                    rationale="x", cycle_id="2026091013")
    result = run_cycle(store, cs, adapter, engine, lambda ctx: hold,
                       settings, NOW + timedelta(hours=1),
                       notifier=avisos.append)
    assert len(adapter.orders) == 1  # a ordem aprovada foi enviada
    assert adapter.orders[0].client_order_id.endswith(
        _json.loads(store.get_decision(pend[0]).order_json)[
            "client_order_id"][-5:])
    assert store.get_pending() == []  # virou executed
    assert any("aprovada" in a for a in avisos)
    store.close()


def test_notifier_que_falha_nao_derruba_ciclo(tmp_path):
    store, cs, adapter, engine, proposer, settings = _fixture(tmp_path)

    def notifier_ruim(texto):
        raise RuntimeError("telegram fora do ar")

    result = run_cycle(store, cs, adapter, engine, proposer, settings, NOW,
                       notifier=notifier_ruim)
    assert result.verdict_status == "approved"  # ciclo completou
    store.close()
```

**Atenção:** o teste existente `test_buy_pequeno_executa_e_poe_stop` passa a conflitar com a regra de primeiro trade (o símbolo nunca foi operado). Ajuste PERMITIDO e obrigatório: nesse teste existente, insira antes do ciclo `store.append_decision(...)` com uma ordem antiga em BTCUSDT (helper local) para que a regra 1 não dispare — mantendo o que o teste verifica (execução + stop). O mesmo vale para `test_sell_cancela_stop_antigo_e_reduz_posicao` (CLOSE não dispara regra 1 — BUY only — então NÃO precisa de ajuste) e `test_ciclo_com_llm_proposer_integrado` (BUY — precisa do mesmo ajuste). Documente no report cada teste ajustado e por quê.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `python3 -m pytest tests/test_cycle.py -v`

- [ ] **Step 3: Implementar**

Em `cycle.py`:

```python
def _execute_approved(store, adapter, notifier, dry_run: bool) -> None:
    if dry_run:
        return
    for decision_id in store.approved_pending():
        record = store.get_decision(decision_id)
        if record is None or not record.order_json:
            store.set_pending_status(decision_id, "executed")
            continue
        order = OrderIntent(**json.loads(record.order_json))
        executed = _execute(store, adapter, order)
        store.set_pending_status(decision_id, "executed")
        _notify(notifier,
                f"✅ ordem aprovada executada: {order.side} {order.qty:g} "
                f"{order.symbol}" if executed else
                f"⚠️ ordem aprovada {decision_id} não executou (IOC sem fill)")


def _notify(notifier, text: str) -> None:
    if notifier is None:
        return
    try:
        notifier(text)
    except Exception:
        pass  # notificação nunca derruba o ciclo
```

Na assinatura: `run_cycle(..., notifier=None, hitl_notifier=None)`. Chamar `_execute_approved` logo após `_expire_pending`. Após `engine.evaluate` e `record_halt_if_needed`: `verdict = apply_hitl_overrides(verdict, proposal, portfolio, store, now)`. No branch NEEDS_APPROVAL: além do `add_pending`, se `hitl_notifier`: try/except chamando `hitl_notifier(decision_id, verdict.order, verdict.reasons)`.

No `main()`: whitelist do store (`stored = store.get_whitelist()`; `whitelist = stored or ALWAYS_INCLUDED`); Telegram quando configurado:

```python
    notifier = hitl_notifier = None
    telegram = None
    if settings.telegram_token and settings.telegram_chat_id:
        from ..telegram.client import TelegramClient
        from ..telegram.format import format_hitl_request
        telegram = TelegramClient(settings.telegram_token,
                                  settings.telegram_chat_id)
        notifier = telegram.send_message

        def hitl_notifier(decision_id, order, reasons):
            telegram.send_message(
                format_hitl_request(decision_id, order, reasons),
                buttons=[("Aprovar", f"ap:{decision_id}"),
                         ("Rejeitar", f"rj:{decision_id}")])
```

Após o ciclo, se `telegram`: enviar `format_cycle_result(result, ordem_do_veredito)` (guarde o order retornando-o? `CycleResult` não carrega a ordem — solução mínima: main relê a última decisão via `store.get_decision(f"{result.cycle_id}-...")`? Simples demais não é; aceite: `format_cycle_result(result, None)` no main — a mensagem de execução detalhada já é coberta pelos notifiers internos; documente). Com `--llm`: após `enrich_news`, buscar `recent_news` com materiality ≥4 cujo primeiro asset esteja em `store.get_positions()` e enviar até 3 `format_material_news` via `_notify`.

README: seção do ciclo ganha o parágrafo do Telegram (push + aprovação por botões; ordens aprovadas executam no ciclo seguinte).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_cycle.py -v` e `python3 -m pytest`
Expected: tudo verde (com os 2 ajustes documentados nos testes de BUY existentes).

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/orchestrator/cycle.py tests/test_cycle.py README.md
git commit -m "feat: ciclo executa aprovados, aplica HITL duro e notifica via Telegram"
```

---

### Task 6: Bot Telegram (comandos + callbacks + digest)

**Files:**
- Create: `src/invest_agent/telegram/bot.py`
- Test: `tests/test_telegram_bot.py`

**Interfaces:**
- Consumes: `TelegramClient` (T1), `build_status_text`/formatadores (T2), `SqliteStore` (T3/2a), `KillSwitch` (Fase 0), `MODERADO`.
- Produces: `handle_update(update: dict, store, kill_switch, client: TelegramClient, now: datetime) -> str | None` (retorna o texto respondido, None se ignorado):
  - `/status` → `build_status_text`; `/perfil` → campos do MODERADO; `/pausar` → `kill_switch.activate("pausado via telegram")`; `/retomar` → `kill_switch.deactivate()` + `store.release_halt()`; `/kill` → `kill_switch.activate("kill via telegram")`;
  - `/aprovar <id>` e `/rejeitar <id>` → `set_pending_status(id, "approved"/"rejected")` SE o id está pendente e não expirado (`expires_at >= now`); expirado → responde "expirada";
  - callback `ap:<id>` / `rj:<id>` → mesmo efeito + `answer_callback`.
  - `run_bot(store, kill_switch, client, clock, once: bool = False)` — loop getUpdates com offset persistente em memória; `once=True` processa um lote e retorna (para teste).
  - `main(argv)`: `--digest` (envia `build_status_text` e sai — cron 9h) ou loop (systemd). Borda: env/relógio.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_telegram_bot.py
import json
from datetime import datetime, timedelta, timezone

from invest_agent.killswitch import KillSwitch
from invest_agent.storage.sqlite_store import SqliteStore
from invest_agent.telegram.bot import handle_update, run_bot
from invest_agent.telegram.client import TelegramClient

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _client(updates_batches):
    sent = []

    def transport(url, body):
        if "getUpdates" in url:
            batch = updates_batches.pop(0) if updates_batches else []
            return json.dumps({"ok": True, "result": batch}).encode()
        if body:
            sent.append(json.loads(body))
        return json.dumps({"ok": True}).encode()

    return TelegramClient("t", "42", transport=transport), sent


def _msg(text):
    return {"update_id": 1, "message": {"chat": {"id": 42}, "text": text}}


def _fx(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    kill = KillSwitch(tmp_path / "KILL")
    client, sent = _client([])
    return store, kill, client, sent


def test_status_e_perfil(tmp_path):
    store, kill, client, sent = _fx(tmp_path)
    out = handle_update(_msg("/status"), store, kill, client, NOW)
    assert "Status" in out
    out = handle_update(_msg("/perfil"), store, kill, client, NOW)
    assert "moderado" in out and "10%" in out
    store.close()


def test_pausar_retomar_kill(tmp_path):
    store, kill, client, sent = _fx(tmp_path)
    handle_update(_msg("/pausar"), store, kill, client, NOW)
    assert kill.is_active()
    handle_update(_msg("/retomar"), store, kill, client, NOW)
    assert not kill.is_active()
    handle_update(_msg("/kill"), store, kill, client, NOW)
    assert kill.is_active() and "kill" in kill.reason()
    store.close()


def test_aprovar_por_comando_e_por_callback(tmp_path):
    store, kill, client, sent = _fx(tmp_path)
    store.add_pending("d1", NOW, NOW + timedelta(minutes=10))
    store.add_pending("d2", NOW, NOW + timedelta(minutes=10))
    handle_update(_msg("/aprovar d1"), store, kill, client, NOW)
    assert store.approved_pending() == ["d1"]
    callback = {"update_id": 2, "callback_query": {
        "id": "cb1", "data": "rj:d2", "message": {"chat": {"id": 42}}}}
    handle_update(callback, store, kill, client, NOW)
    assert store.get_pending() == []  # d1 approved, d2 rejected
    store.close()


def test_aprovar_expirada_recusa(tmp_path):
    store, kill, client, sent = _fx(tmp_path)
    store.add_pending("d1", NOW - timedelta(hours=1),
                      NOW - timedelta(minutes=50))
    out = handle_update(_msg("/aprovar d1"), store, kill, client, NOW)
    assert "expirada" in out
    assert store.approved_pending() == []
    store.close()


def test_comando_desconhecido(tmp_path):
    store, kill, client, sent = _fx(tmp_path)
    out = handle_update(_msg("/foo"), store, kill, client, NOW)
    assert "comandos" in out.lower()
    store.close()


def test_run_bot_once_processa_lote(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    kill = KillSwitch(tmp_path / "KILL")
    client, sent = _client([[_msg("/status")]])
    run_bot(store, kill, client, clock=lambda: NOW, once=True)
    assert any("Status" in m.get("text", "") for m in sent)
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_telegram_bot.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/telegram/bot.py
"""Bot de comandos (spec §4.6): /status /perfil /pausar /retomar /kill
/aprovar /rejeitar + botões inline. Processo separado do ciclo (systemd);
o bot só marca aprovações — a execução acontece no ciclo seguinte.
Kill switch e halt são arquivos/SQLite compartilhados — nenhum estado
em memória além do offset."""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..config import MODERADO
from ..killswitch import KillSwitch
from ..storage.sqlite_store import SqliteStore
from .client import TelegramClient
from .format import build_status_text

AJUDA = ("Comandos: /status /perfil /pausar /retomar /kill "
         "/aprovar <id> /rejeitar <id>")


def _perfil() -> str:
    p = MODERADO
    return (f"Perfil {p.name}: máx {p.max_position_pct:.0%} por ativo · "
            f"máx {p.max_exposure_pct:.0%} investido · stop "
            f"{p.stop_loss_pct:.0%} · ≤{p.max_orders_per_day} ordens/dia · "
            f"cooldown {p.cooldown_hours:g}h · HITL "
            f"{p.hitl_threshold_pct:.0%} · halt "
            f"{p.daily_loss_halt_pct:.0%}/{p.weekly_loss_halt_pct:.0%}/"
            f"{p.monthly_loss_halt_pct:.0%}")


def _decide(store: SqliteStore, decision_id: str, status: str,
            now: datetime) -> str:
    pending = {p[0]: p for p in store.get_pending()}
    entry = pending.get(decision_id)
    if entry is None:
        return f"pendência {decision_id} não encontrada (já decidida?)"
    if entry[2] < now:
        store.set_pending_status(decision_id, "expired")
        return f"pendência {decision_id} expirada — ordem cancelada"
    store.set_pending_status(decision_id, status)
    verbo = "aprovada" if status == "approved" else "rejeitada"
    extra = (" — executa no próximo ciclo" if status == "approved" else "")
    return f"pendência {decision_id} {verbo}{extra}"


def handle_update(update: dict, store: SqliteStore, kill_switch: KillSwitch,
                  client: TelegramClient, now: datetime) -> str | None:
    callback = update.get("callback_query")
    if callback:
        data = callback.get("data", "")
        client.answer_callback(callback["id"])
        if data.startswith("ap:"):
            reply = _decide(store, data[3:], "approved", now)
        elif data.startswith("rj:"):
            reply = _decide(store, data[3:], "rejected", now)
        else:
            reply = AJUDA
        client.send_message(reply)
        return reply

    text = (update.get("message") or {}).get("text", "").strip()
    if not text:
        return None
    parts = text.split()
    command, args = parts[0], parts[1:]
    if command == "/status":
        reply = build_status_text(store, MODERADO, now)
    elif command == "/perfil":
        reply = _perfil()
    elif command == "/pausar":
        kill_switch.activate("pausado via telegram")
        reply = "⏸️ pausado — kill switch ativo"
    elif command == "/retomar":
        kill_switch.deactivate()
        store.release_halt()
        reply = "▶️ retomado — kill switch e halt liberados"
    elif command == "/kill":
        kill_switch.activate("kill via telegram")
        reply = "🛑 kill switch ATIVADO"
    elif command == "/aprovar" and args:
        reply = _decide(store, args[0], "approved", now)
    elif command == "/rejeitar" and args:
        reply = _decide(store, args[0], "rejected", now)
    else:
        reply = AJUDA
    client.send_message(reply)
    return reply


def run_bot(store: SqliteStore, kill_switch: KillSwitch,
            client: TelegramClient, clock: Callable[[], datetime],
            once: bool = False) -> None:
    offset = 0
    while True:
        updates = client.get_updates(offset)
        for update in updates:
            offset = max(offset, update["update_id"] + 1)
            handle_update(update, store, kill_switch, client, clock())
        if once:
            return


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bot Telegram do agente")
    parser.add_argument("--digest", action="store_true",
                        help="envia o status e sai (cron 9h)")
    args = parser.parse_args(argv)

    from ..settings import Settings

    settings = Settings.from_env(os.environ)  # borda única
    if not settings.telegram_token or not settings.telegram_chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ausentes")
    store = SqliteStore(settings.db_path)
    client = TelegramClient(settings.telegram_token,
                            settings.telegram_chat_id)
    if args.digest:
        client.send_message(build_status_text(
            store, MODERADO, datetime.now(timezone.utc)))
        store.close()
        return
    kill_switch = KillSwitch(settings.kill_switch_path)
    try:
        run_bot(store, kill_switch, client,
                clock=lambda: datetime.now(timezone.utc))
    finally:
        store.close()


if __name__ == "__main__":
    main()
```

(Nota: `time` importado só se necessário; remova imports não usados.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_telegram_bot.py -v` e `python3 -m pytest`
Expected: tudo verde.

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/telegram/bot.py tests/test_telegram_bot.py
git commit -m "feat: bot Telegram com comandos, aprovação por botões e digest"
```

---

### Task 7: Job semanal da whitelist

**Files:**
- Create: `src/invest_agent/jobs/__init__.py`
- Create: `src/invest_agent/jobs/whitelist.py`
- Test: `tests/test_jobs_whitelist.py`

**Interfaces:**
- Consumes: `fetch_symbol_stats` (1a), `build_whitelist` (Fase 0), `SqliteStore.set_whitelist` (T3).
- Produces: `refresh_whitelist(client, store, now) -> frozenset[str]` (fetch → build → persist → retorna) e `main(argv)` (`python3 -m invest_agent.jobs.whitelist` — cron semanal; borda com BinanceMarketData real e env).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jobs_whitelist.py
from datetime import datetime, timedelta, timezone

from invest_agent.data.models import Candle
from invest_agent.jobs.whitelist import refresh_whitelist
from invest_agent.storage.sqlite_store import SqliteStore

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _daily(symbol, days_ago, quote_volume):
    open_time = NOW - timedelta(days=days_ago)
    return Candle(symbol=symbol, interval="1d", open_time=open_time,
                  open=1.0, high=2.0, low=0.5, close=1.5, volume=1.0,
                  quote_volume=quote_volume, n_trades=1,
                  close_time=open_time + timedelta(days=1))


class FakeClient:
    def exchange_info(self):
        return {"symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC",
             "quoteAsset": "USDT"},
            {"symbol": "SOLUSDT", "status": "TRADING", "baseAsset": "SOL",
             "quoteAsset": "USDT"},
        ]}

    def klines(self, symbol, interval, start=None, end=None, limit=1000):
        if limit == 1:
            return [_daily(symbol, 3000, 1.0)]
        return [_daily(symbol, d, 100.0) for d in range(1, 31)]


def test_refresh_whitelist_persiste(tmp_path):
    store = SqliteStore(tmp_path / "a.db")
    out = refresh_whitelist(FakeClient(), store, NOW)
    assert {"BTCUSDT", "ETHUSDT", "SOLUSDT"} <= out  # ETH via ALWAYS_INCLUDED
    assert store.get_whitelist() == out
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_jobs_whitelist.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/invest_agent/jobs/whitelist.py
"""Job semanal (spec §4.4): recalcula a whitelist por critérios e
persiste no SQLite — o ciclo passa a usá-la automaticamente."""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from ..data.symbol_stats import fetch_symbol_stats
from ..storage.sqlite_store import SqliteStore
from ..whitelist import build_whitelist


def refresh_whitelist(client, store: SqliteStore,
                      now: datetime) -> frozenset[str]:
    stats = fetch_symbol_stats(client, now)
    symbols = build_whitelist(stats)
    store.set_whitelist(sorted(symbols), now)
    return symbols


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Recalcula a whitelist")
    parser.add_argument("--db", default=Path("data/agent.db"), type=Path)
    args = parser.parse_args(argv)

    from ..data.binance_client import BinanceMarketData
    from ..settings import Settings

    settings = Settings.from_env(os.environ)
    client = BinanceMarketData(base_url=settings.binance_base_url)
    store = SqliteStore(args.db)
    now = datetime.now(timezone.utc)  # borda
    symbols = refresh_whitelist(client, store, now)
    store.close()
    print(f"whitelist atualizada ({len(symbols)}): "
          + ", ".join(sorted(symbols)))


if __name__ == "__main__":
    main()
```

`src/invest_agent/jobs/__init__.py`: vazio.

**Nota:** `BinanceMarketData` do 1a usa o host público para klines; `exchange_info` na testnet pode divergir do spot real — o runbook (T8) manda rodar este job contra o host de PRODUÇÃO (`BINANCE_BASE_URL=https://api.binance.com`) mesmo em fase de paper, pois é só leitura pública.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_jobs_whitelist.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/invest_agent/jobs/ tests/test_jobs_whitelist.py
git commit -m "feat: job semanal da whitelist persistida"
```

---

### Task 8: Runbook de operação + README final

**Files:**
- Create: `docs/ops.md`
- Modify: `README.md`

**Interfaces:** documentação — sem código novo. Conteúdo obrigatório do `docs/ops.md` (runbook, pt-BR):

1. **Pré-requisitos:** VPS Linux (ex.: Hetzner CX22), IP estático, Python ≥3.12, `pip install duckdb pyarrow backtrader anthropic`, clone do repo.
2. **Credenciais (env):** tabela com TODAS as vars de `Settings.from_env` e onde obtê-las — chaves da TESTNET Binance (testnet.binance.vision, login GitHub), chave real depois (SEM permissão de saque + IP whitelist; Ed25519 recomendado ao ir a live — o adapter usa HMAC, anotar migração), `TELEGRAM_BOT_TOKEN` (@BotFather), `TELEGRAM_CHAT_ID` (@userinfobot), `ANTHROPIC_API_KEY`, `API_COST_DAILY_CAP_USD`.
3. **Bootstrap:** `python3 -m invest_agent.data.ingest --symbol ... --interval 1h --since ...` para cada símbolo da whitelist; `python3 -m invest_agent.jobs.whitelist`; `python3 -m invest_agent.news.ingest --macro`; primeiro ciclo com `--dry-run`.
4. **Cron (exemplos literais):**
   ```cron
   # ORDEM IMPORTA: candles aos :01 (vela da hora acabou de fechar), ciclo aos :05 —
   # o gate de qualidade rejeita candle com mais de 600s (spec); fora dessa janela o ciclo reprova por dado velho.
   1 * * * *   cd /opt/invest-agent && for s in $(python3 -c "from invest_agent.storage.sqlite_store import SqliteStore; s=SqliteStore('data/agent.db'); print(' '.join(sorted(s.get_whitelist()))); s.close()"); do python3 -m invest_agent.data.ingest --symbol $s --interval 1h; done >> logs/candles.log 2>&1
   5 * * * *   cd /opt/invest-agent && python3 -m invest_agent.orchestrator.cycle --llm >> logs/cycle.log 2>&1
   */15 * * * * cd /opt/invest-agent && python3 -m invest_agent.news.ingest >> logs/news.log 2>&1
   10 6 * * *  cd /opt/invest-agent && python3 -m invest_agent.news.ingest --macro >> logs/macro.log 2>&1
   0 5 * * 1   cd /opt/invest-agent && python3 -m invest_agent.jobs.whitelist >> logs/whitelist.log 2>&1
   0 9 * * *   cd /opt/invest-agent && python3 -m invest_agent.telegram.bot --digest >> logs/digest.log 2>&1
   0 22 * * 0  cd /opt/invest-agent && python3 -m invest_agent.brain.weekly --submit >> logs/weekly.log 2>&1
   0 10 * * 1  cd /opt/invest-agent && python3 -m invest_agent.brain.weekly --collect $(cat data/last_batch_id 2>/dev/null) >> logs/weekly.log 2>&1
   ```
   (nota: --submit imprime o batch id; redirecionar p/ data/last_batch_id via wrapper simples é sugestão do runbook)
5. **systemd** unit para o bot (`invest-agent-bot.service`: ExecStart=`python3 -m invest_agent.telegram.bot`, Restart=always, EnvironmentFile=/etc/invest-agent.env).
6. **Dead-man switch:** cron a cada 10 min: se `heartbeat` mais velho que 2h → ativa kill switch e manda Telegram (script inline de 5 linhas no runbook).
7. **Operação:** comandos do bot; fluxo HITL (botões → executa no ciclo seguinte; para agir já: rodar `python3 -m invest_agent.orchestrator.cycle --llm` manualmente); o que fazer em halt MONTH (/retomar só depois de revisar).
8. **Gates das fases (spec §5):** fase 2 = 1-3 meses de paper na testnet, 30 dias sem incidente não tratado; fase 3 = trocar `BINANCE_BASE_URL` para produção com chave real de R$ 1.000, ≥1 mês de métricas; fase 4 = escala/opções, só com evidência. Checklist de transição testnet→live (trocar env, IP whitelist, chave sem saque, conferir filtros de LOT_SIZE/NOTIONAL do símbolo — anotado como limitação: o adapter não aplica exchange filters de tick/step size; ordens podem ser rejeitadas pela exchange com precisão inválida — arredondar qty manualmente se ocorrer, melhoria futura).
9. **Extensões futuras anotadas:** chat livre no bot (LLM read-only), OCO com take-profit, Ed25519, exchange filters automáticos, embedding dedupe, módulo 2 (opções EUA).

README: atualizar tabela de fases (2 = "✅ código concluído² " com nota "² gates operacionais: 1-3 meses de paper — ver docs/ops.md"); seção "🧭 Operação" apontando para docs/ops.md; revisar "Limitações conhecidas": REMOVER os itens de HITL primeiro-trade e pós-breaker (implementados na 2c) e ADICIONAR exchange filters (LOT_SIZE) como limitação atual.

- [ ] **Step 1: Escrever docs/ops.md com TODO o conteúdo acima** (sem placeholders — cron literal, unit literal, script do dead-man literal).
- [ ] **Step 2: Atualizar README.md** (tabela de fases, seção Operação, limitações).
- [ ] **Step 3: Rodar a suíte** (`python3 -m pytest`) — docs não quebram nada, mas o commit exige suíte verde.
- [ ] **Step 4: Commit**

```bash
git add docs/ops.md README.md
git commit -m "docs: runbook de operação (VPS, cron, systemd, gates das fases)"
```

---

## Self-review do plano (executada na escrita)

- **Cobertura da spec (escopo 2c):** §4.6 push (ordem/fill/breaker/notícia ≥4/digest 9h) → T2/T5/T6; comandos + botões + único chat → T1/T6; HITL botões com TTL → T5/T6 (expiração já era da 2a); §4.4 HITL obrigatórios (primeiro trade, pós-breaker) → T4/T5 — fecha os deferidos das fases 0/2a; whitelist semanal §4.4 → T7 + main do ciclo (T5); §5 gates/runbook → T8. Chat livre: fora de escopo por ruling (documentado).
- **Placeholders:** nenhum nos códigos; T8 é documentação com conteúdo obrigatório enumerado (o implementer escreve o texto integral).
- **Consistência de tipos:** `TelegramClient.send_message(text, buttons)` usado em T5/T6; `handle_update(update, store, kill_switch, client, now)`; `set_pending_status`/`get_pending` (2a) e `approved_pending`/`get_decision` (T3) casam; `apply_hitl_overrides(verdict, proposal, portfolio, store, now)` chamado no ciclo (T5) na ordem definida; `OrderIntent(**json.loads(order_json))` — campos do asdict da 2a batem 1:1 com o construtor; `run_bot(..., once=True)` p/ teste.
- **Interações com testes existentes:** T5 documenta os DOIS testes de BUY existentes que precisam de uma ordem histórica para não disparar a regra de primeiro trade — ajuste obrigatório e justificado (a regra nova é spec §4.4; os testes verificavam execução, não a ausência de HITL).
- **Risco conhecido:** o bot roda em processo separado do ciclo com SQLite compartilhado — SQLite serializa escrituras; contenção é improvável (bot escreve raramente), documentado no runbook.
