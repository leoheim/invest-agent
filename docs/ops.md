# 🛠️ Runbook de operação — invest-agent

Guia operacional para rodar o agente em VPS: provisionamento, credenciais,
cron, systemd, dead-man switch, comandos do dia a dia e os gates que
definem quando avançar de fase. Complementa a spec
([`docs/superpowers/specs/2026-09-05-invest-agent-design.md`](superpowers/specs/2026-09-05-invest-agent-design.md))
— este documento é sobre **como operar**, não sobre **como o motor decide**.

## 1. Pré-requisitos

- VPS Linux (ex.: Hetzner CX22 — 2 vCPU/4GB é suficiente; não há treino de
  modelo local).
- IP estático — necessário para o whitelist de IP na Binance quando for a
  chave real.
- Python ≥ 3.12.
- Dependências: `pip install duckdb pyarrow backtrader anthropic pytz` (o
  restante do runtime é stdlib — spec §4.5, zero dependências
  desnecessárias).
- Clone do repositório em `/opt/invest-agent` (caminho usado nos exemplos
  de cron/systemd abaixo; ajuste se usar outro).

```bash
git clone <repo> /opt/invest-agent
cd /opt/invest-agent
python3 -m venv .venv && source .venv/bin/activate
pip install duckdb pyarrow backtrader anthropic pytz pytest
mkdir -p data logs
```

## 2. Credenciais (variáveis de ambiente)

Todas as variáveis lidas por `Settings.from_env` (`src/invest_agent/settings.py`).
Nenhuma delas tem valor de exemplo funcional — preencha com as suas.
Guarde-as em `/etc/invest-agent.env` (permissão `600`, dono do serviço) —
nunca no repositório, nunca em log (spec §4.5: secrets em env, jamais em
repo/prompt/log; `Settings` usa `repr=False` nos campos secretos para não
vazar em traceback).

| Variável | Obrigatória | Onde obter | Observações |
|---|:---:|---|---|
| `BINANCE_API_KEY` | sim | testnet: [testnet.binance.vision](https://testnet.binance.vision) (login via GitHub) → gera par de chaves de teste. Produção: binance.com → API Management. | Chave real: **sem permissão de saque**, com IP whitelist. Ver checklist de transição (§8). |
| `BINANCE_API_SECRET` | sim | mesma tela da API key. | Nunca versionar; nunca logar. |
| `BINANCE_BASE_URL` | não (default `https://testnet.binance.vision`) | — | Trocar para `https://api.binance.com` só na transição para live (§8) e para rodar o job de whitelist (§3/nota do T7 — leitura pública de produção mesmo em fase paper). |
| `TELEGRAM_BOT_TOKEN` | sim | [@BotFather](https://t.me/BotFather) → `/newbot`. | Token do bot; nunca aparece em logs (cliente HTTP stdlib garante isso — spec §4.6). |
| `TELEGRAM_CHAT_ID` | sim | [@userinfobot](https://t.me/userinfobot) → manda `/start`, ele responde com o `id`. | Um único chat autorizado — updates de qualquer outro `chat_id` são descartados pelo cliente. |
| `ANTHROPIC_API_KEY` | sim (para `--llm`) | [console.anthropic.com](https://console.anthropic.com) → API Keys. | Sem ela, o ciclo roda em modo HOLD determinístico (sem cérebro). |
| `API_COST_DAILY_CAP_USD` | não (default `2.0`) | escolha sua própria | Teto diário de gasto com API do LLM — acima disso, o ciclo do dia para de chamar o cérebro. |
| `INVEST_DB_PATH` | não (default `data/agent.db`) | — | Caminho do SQLite (decision log, posições, halt, whitelist, pendências). |
| `INVEST_CANDLES_ROOT` | não (default `data/candles`) | — | Raiz do Parquet particionado de candles. |
| `INVEST_KILL_PATH` | não (default `data/KILL`) | — | Arquivo do kill switch — existir = parado. |
| `INVEST_HEARTBEAT_PATH` | não (default `data/heartbeat`) | — | Timestamp do último ciclo vivo — consumido pelo dead-man switch (§6). |

**Migração de assinatura (anotado, não implementado):** o adapter de
execução (`execution/binance_adapter.py`) assina requisições com HMAC
(`BINANCE_API_SECRET` simétrico). A Binance recomenda **Ed25519** para
chaves novas — assinatura por chave privada, sem segredo compartilhado em
trânsito. Ao gerar a chave real de produção, prefira Ed25519 e trate a
troca do adapter de HMAC→Ed25519 como item de melhoria futura (§9); até
lá, HMAC com a chave sem permissão de saque é aceitável.

## 3. Bootstrap (primeira vez)

```bash
# 1) histórico de candles para cada símbolo da whitelist (ajuste a lista/--since)
for s in BTCUSDT ETHUSDT SOLUSDT; do
  python3 -m invest_agent.data.ingest --symbol $s --interval 1h --since 2024-01-01
done

# 2) calcula e persiste a whitelist real (roda contra produção — só leitura pública, ver §4)
BINANCE_BASE_URL=https://api.binance.com python3 -m invest_agent.jobs.whitelist

# 3) carga inicial de notícias + macro
python3 -m invest_agent.news.ingest --macro

# 4) primeiro ciclo em modo seguro — não envia ordem nenhuma
python3 -m invest_agent.orchestrator.cycle --dry-run
```

Só depois de conferir a saída do `--dry-run` (proposta, veredito, motivos)
é que o cron (§4) deve ser instalado.

## 4. Cron

Antes de ativar o cron, configure o caminho de busca e as variáveis de
ambiente. Adicione ao topo do crontab (`crontab -e`):

```bash
PATH=/opt/invest-agent/.venv/bin:/usr/bin:/bin
```

Crie o wrapper de carregamento (`/opt/invest-agent/bin/with-env`, `chmod +x`):

```bash
#!/bin/sh
set -a
. /etc/invest-agent.env
set +a
exec "$@"
```

Cada comando do cron abaixo deve ser prefixado com esse wrapper. Exemplo (adaptado
da linha de news do bloco abaixo):

```bash
*/15 * * * * cd /opt/invest-agent && /opt/invest-agent/bin/with-env python3 -m invest_agent.news.ingest >> logs/news.log 2>&1
```

Sem isso: `--llm` roda sem cérebro (ANTHROPIC_API_KEY vazia), a whitelist consulta a testnet (BINANCE_BASE_URL default) e o dead-man não consegue alertar no Telegram.

Os comandos abaixo — copie-os para o crontab com o wrapper prefixado a cada linha:

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

(nota: `--submit` imprime o batch id; redirecionar p/ `data/last_batch_id`
via wrapper simples é sugestão do runbook — por exemplo, trocar a linha de
domingo 22h por um script de uma linha que faz
`python3 -m invest_agent.brain.weekly --submit | tee -a logs/weekly.log | tail -1 > data/last_batch_id`.)

O bot (`/status /perfil /pausar /retomar /kill /aprovar /rejeitar` +
botões) **não entra no cron** — ele roda continuamente via systemd (§5),
fazendo long-poll no `getUpdates` da Bot API.

## 5. systemd (bot Telegram)

Crie o usuário do sistema e ajuste permissões:

```bash
sudo useradd --system --create-home --home-dir /opt/invest-agent invest-agent
sudo chown -R invest-agent: /opt/invest-agent
```

Arquivo unit `/etc/systemd/system/invest-agent-bot.service`:

```ini
[Unit]
Description=invest-agent — bot Telegram (comandos, botões, digest)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=invest-agent
WorkingDirectory=/opt/invest-agent
EnvironmentFile=/etc/invest-agent.env
ExecStart=/opt/invest-agent/.venv/bin/python3 -m invest_agent.telegram.bot
Restart=always
RestartSec=10
StandardOutput=append:/opt/invest-agent/logs/bot.log
StandardError=append:/opt/invest-agent/logs/bot.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now invest-agent-bot.service
sudo systemctl status invest-agent-bot.service
```

`Restart=always` cobre crash do processo; o kill switch e o halt vivem em
arquivo/SQLite fora do processo, então um restart do bot não perde estado
(spec — kill switch sobrevive a matar o processo, de propósito).

## 6. Dead-man switch

Cron a cada 10 minutos: se o heartbeat do ciclo estiver mais velho que 2h
(2 ciclos perdidos, folga sobre o cron horário), ativa o kill switch e
avisa via Telegram. Script inline, sem dependência nova:

```cron
*/10 * * * * cd /opt/invest-agent && python3 -c "
from datetime import datetime, timezone
from invest_agent.settings import Settings
from invest_agent.killswitch import KillSwitch, heartbeat_stale
import os, json, urllib.request
s = Settings.from_env(os.environ)
if heartbeat_stale(s.heartbeat_path, 7200, datetime.now(timezone.utc)):
    KillSwitch(s.kill_switch_path).activate('dead-man switch: heartbeat parado')
    body = json.dumps({'chat_id': s.telegram_chat_id, 'text': '🛑 dead-man switch: heartbeat parado há mais de 2h — kill switch ativado'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{s.telegram_token}/sendMessage', data=body, headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(req, timeout=10)
" >> logs/deadman.log 2>&1
```

Falha fechada por construção: sem arquivo de heartbeat, `heartbeat_stale`
retorna `True` (ver `killswitch.py`) — silêncio total também aciona o
kill switch, não só heartbeat velho.

## 7. Operação

**Comandos do bot** (Telegram, chat único autorizado):

| Comando | Efeito |
|---|---|
| `/status` | posições registradas, halt ativo, custo de API do dia, pendências. |
| `/perfil` | parâmetros do perfil de risco ativo (moderado). |
| `/pausar` | ativa o kill switch (`"pausado via telegram"`) — nenhuma ordem nova até `/retomar`. |
| `/retomar` | desativa o kill switch **e** libera o halt persistido. |
| `/kill` | ativa o kill switch com motivo `"kill via telegram"` — mesmo efeito de `/pausar`, semântica de emergência. |
| `/aprovar <id>` / `/rejeitar <id>` | decide uma pendência HITL pelo id (também disponível como botões inline `ap:<id>`/`rj:<id>` na mensagem original). Id expirado → responde "expirada", não executa. |

**Fluxo HITL:** o ciclo detecta a necessidade de aprovação (ordem grande,
primeiro trade em ativo novo, ou primeira ordem pós-circuit-breaker — spec
§4.4) e manda a proposta ao Telegram com botões. Aprovar/rejeitar só marca
o status no store — **a ordem em si só é enviada no próximo ciclo
agendado** (cron de 5 em 5 minutos na hora, ou o próximo `:05`). Para
agir imediatamente após aprovar (não esperar o próximo slot do cron):

```bash
cd /opt/invest-agent && python3 -m invest_agent.orchestrator.cycle --llm
```

**Halt de nível MONTH:** é o mais severo dos circuit breakers (drawdown
mensal ≥ 15%, perfil moderado). **Não rode `/retomar` de reflexo** — revise
manualmente o `decision_log` (`read_decisions()` / SQLite direto) e o
motivo do halt antes de liberar. Halts de nível DAY/WEEK são esperados
fazerem parte da operação normal; MONTH é sinal de que algo saiu do
script e merece uma revisão de verdade antes de destravar.

## 8. Gates das fases (spec §5)

- **Fase 2 (atual — paper trading):** 1 a 3 meses rodando na testnet
  Binance, sem intervenção manual fora do fluxo HITL normal, **30 dias
  corridos sem incidente não tratado** (kill switch acionado por bug,
  perda de dados, ordem incorreta etc. conta como incidente; halt de
  circuit breaker tratado corretamente não conta).
- **Fase 3 (live micro):** trocar `BINANCE_BASE_URL` para produção
  (`https://api.binance.com`) com chave real e capital de R$ 1.000.
  Gate de entrada: fase 2 cumprida + ≥ 1 mês de métricas de paper
  revisadas (weekly review do Opus, decision log) mostrando comportamento
  dentro do esperado.
- **Fase 4 (escala/opções):** escala gradual de capital e módulo de
  opções EUA (paper primeiro) — só com evidência de fase 3 (métricas
  reais, não simuladas) sustentando a decisão.

**Checklist de transição testnet → live:**

1. Gerar chave de API de produção **sem permissão de saque**.
2. Configurar IP whitelist da VPS na chave.
3. Trocar `BINANCE_API_KEY`/`BINANCE_API_SECRET`/`BINANCE_BASE_URL` no
   `/etc/invest-agent.env` (nunca no repo).
4. **Conferir os filtros `LOT_SIZE`/`NOTIONAL` do símbolo** via
   `exchange_info` antes de operar — **limitação atual:** o adapter de
   execução não aplica os exchange filters de tick size/step size da
   Binance. Uma ordem pode ser rejeitada pela exchange por precisão
   inválida de quantidade/preço; se ocorrer, arredonde `qty`/`price`
   manualmente conforme o filtro do símbolo até a correção automática
   ser implementada (§9).
5. Rodar `--dry-run` uma vez contra produção antes de religar o cron
   real, só para conferir que a leitura de mercado/carteira está correta.
6. Reiniciar o serviço systemd do bot com o novo `EnvironmentFile`.

## 9. Extensões futuras anotadas

- **Chat livre no bot:** hoje só comandos fixos; permitir perguntas
  livres ao dono via LLM **somente leitura** (sem side effects) fica para
  depois — fora do escopo desta fase por decisão explícita.
- **OCO com take-profit:** hoje só stop-loss é colocado na exchange junto
  da entrada; adicionar a perna de take-profit (ordem OCO completa) é
  melhoria futura.
- **Ed25519:** substituir a assinatura HMAC do adapter de execução por
  Ed25519 (ver nota na tabela de credenciais, §2).
- **Exchange filters automáticos:** aplicar `LOT_SIZE`/`PRICE_FILTER`/
  `NOTIONAL` de `exchange_info` automaticamente no adapter, em vez de
  ajuste manual (ver checklist §8, item 4).
- **Dedupe de notícias por embedding:** hoje o dedupe é só URL canônica +
  SimHash de título; dedupe semântico por embedding fica para a Fase 2+.
- **Módulo 2 (opções EUA):** escopo da Fase 4, não iniciado.

## 10. Débito técnico herdado (itens conhecidos, não bloqueantes desta fase)

Lista viva de pendências reais deixadas por fases anteriores — o dono
deve conhecê-las antes de operar em live. Nenhuma delas impede a operação
em paper trading; todas merecem revisão antes da Fase 3.

- **Reconcile-on-boot (spec §4.5):** existe `get_order` para reconciliar
  o estado de uma ordem com a exchange, mas nada chama isso na
  inicialização do processo. Um crash entre o `POST` da ordem e a
  persistência do resultado pode perder um fill (o agente não saberia
  que a ordem foi executada).
- **Reason do cancel engolido:** quando o cancelamento de um stop falha,
  o erro é engolido silenciosamente — gap de observabilidade; não há hoje
  como saber, só olhando log, que um stop deixou de ser cancelado como
  deveria.
- **Breaker de taxa de erro de tools (spec §4.4):** não implementado —
  não há hoje um circuit breaker específico para taxa de erro nas
  chamadas de ferramentas do LLM.
- **Cap de exposição usa avg_price, não mark-to-market:** o motor de
  regras avalia o teto de exposição total (60%) a preço médio de entrada,
  não a preço de mercado atual — subestima exposição real quando posições
  valorizaram (mesma limitação já anotada desde a Fase 0, ainda não
  fechada).
- **Re-armar stop ausente:** janela em que o stop é colocado na exchange
  mas o processo morre antes de persistir o id do stop localmente — na
  próxima subida, o agente não sabe que aquele stop existe.
- **Timeout explícito no `LlmClient` ausente:** o timeout default do SDK
  da Anthropic pode segurar um ciclo inteiro por ~30 minutos em caso de
  requisição pendurada.
- **Hardening pós-response do `LlmClient` ausente:** uma falha ao
  processar a response *depois* da chamada (fora do bloco `try` da
  chamada em si) vira uma exceção crua, sem tratamento específico.
- **`unenriched_news` processa mais antigas primeiro:** sob backlog de
  enriquecimento, uma notícia nova pode chegar e ficar esperando sem
  enriquecimento enquanto notícias antigas são processadas primeiro.
- **Collect da revisão semanal não distingue estados do batch:** um batch
  `errored`/`expired` é tratado como `processing` (fica tentando de novo
  indefinidamente) e uma resposta de `refusal` do modelo vira um
  `learning` vazio em vez de ser sinalizada como falha.
- **Cache de 1h do system prompt abaixo do prefixo mínimo cacheável:** o
  prompt de sistema ainda não atingiu o tamanho mínimo para o
  prompt caching da Anthropic ser efetivo — hoje é um no-op silencioso;
  vai passar a valer sozinho quando os prompts crescerem, sem precisar de
  ação.

Ver também §9 (extensões futuras já com decisão de escopo, diferente
desta lista de débitos que ficaram implicitamente para trás).
