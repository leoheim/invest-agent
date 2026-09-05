# Spec: Agente de Investimento Autônomo — cripto primeiro, opções depois

**Data:** 2026-09-05 · **Status:** aprovado pelo dono do projeto · **Fase atual:** design → plano de implementação

## 1. Objetivo

Um agente pessoal que opera a conta de investimento do próprio dono de forma autônoma dentro de limites rígidos: analisa preços, notícias e contexto macro com modelos Claude, propõe operações, executa apenas o que um motor de regras determinístico aprovar, e mantém o dono informado e no controle via Telegram.

Não é gestão de recursos de terceiros (o que exigiria autorização da CVM). É automação da própria conta, o que é legal no Brasil.

## 2. Decisões travadas (2026-09-05)

| Decisão | Escolha | Racional |
|---|---|---|
| Mercado inicial | **Cripto spot na Binance** | Menor fricção p/ brasileiro: Pix, bots permitidos, testnet grátis, isenção IR até R$35k/mês em vendas, mercado 24/7. CVM proíbe derivativos da Binance no BR → **spot apenas** |
| Opções | **Módulo 2, previsto desde já** | Opções de cripto estão sendo fechadas p/ brasileiros (Bybit liquida 21/09/2026; prazo BCB 20/10/2026). Módulo 2 = **opções EUA** via tastytrade (Brasil aceito) ou Alpaca, só estruturas de risco definido, ativado apenas após o núcleo provar-se em live |
| Perfil de risco | **Moderado** | Máx 10%/ativo, máx 60% investido, halt a −5% dia / −10% semana / −15% mês |
| Canal | **Telegram** | Grátis, sem janela de 24h, sem CNPJ, botões nativos. WhatsApp inviável hoje (ban de AI assistants pela Meta em 2026 + janela 24h + CNPJ); canal abstraído p/ adaptador futuro |
| Modelos | Sonnet 5 (decisão) + Haiku 4.5 (triagem) + Opus 5 (revisão semanal, batch) | Ponto ótimo custo/qualidade; ~US$35/mês com prompt caching TTL 1h |
| Infra | VPS Linux (Hetzner CX22, €4,50/mês), IP estático | IP whitelist da Binance exige IP fixo (sem ela, permissão de trading expira em 90 dias) |
| Linguagem | Python | Ecossistema: binance-connector, CCXT, vectorbt/backtrader, python-telegram-bot |
| Persistência | SQLite (estado+log) · Parquet+DuckDB (candles) · markdown+git (aprendizados do agente) | Zero operação; padrão validado na pesquisa |

## 3. Princípio arquitetural inegociável

> **O LLM nunca executa. O LLM propõe uma estrutura tipada (JSON validado por schema); código puro, testado e sem LLM decide se vira ordem.** O LLM não vê chave de API, não calcula tamanho de posição, e o estado da conta é read-only para ele.

Fundamentação: todos os experimentos públicos com LLM decidindo sozinho perderam dinheiro (Alpha Arena: 6/32 lucrativos); o padrão de duas camadas (Hummingbot Condor, FinRobot) é o único que sobrevive. Pesquisa completa em `~/Documents/research/` (6 relatórios).

## 4. Componentes

### 4.1 Ingestão (cron, sem LLM)
- Candles Binance a cada 1–5 min (REST klines; histórico via data.binance.vision desde 2017)
- RSS a cada 5–15 min: InfoMoney, Valor (pox.globo.com/rss/valor), MoneyTimes, CoinDesk, CoinTelegraph + Google News RSS por ativo (`when:1d`, pt-BR)
- Fear & Greed (api.alternative.me/fng) 1×/dia · BCB SGS (Selic 432, câmbio 1) 1×/dia
- Pipeline: normalizar → dedupe (URL canônica → SimHash título → embedding >0.92) → **triagem por keyword antes do LLM** → enriquecimento em batch (resumo, sentimento, materialidade 1–5, JSON) → persistir com `published_at` ≠ `ingested_at` (anti look-ahead)
- Custo de dados: R$ 0

### 4.2 Memória
- **SQLite**: posições, ordens, log de decisões **append-only** (decision_id, snapshot de inputs com hash, proposta, veredito do motor, ordem, fills, custo de API) — serve para debug, auditoria e IR
- **Parquet+DuckDB**: candles históricos para backtest
- **`learnings/*.md` em git**: o agente lê no início do ciclo e escreve aprendizados ao final (journaling); revisão semanal do Opus consolida
- Índice vetorial local (sqlite-vec ou Chroma) para RAG de notícias/decisões passadas

### 4.3 Cérebro (workflow fixo, não agente livre)
Ciclo padrão a cada **1h** (configurável):
1. Código monta snapshot: candles+indicadores (calculados em Python), notícias novas enriquecidas, posições, PnL, regime
2. **Haiku 4.5** triagem da whitelist → seleciona ≤3 candidatos
3. **Sonnet 5** (effort medium, prompt caching TTL 1h) → **proposta**: `{ativo, ação: comprar|vender|manter|fechar, convicção: 0–1, racional, urgência}` via json_schema strict
4. Checar `stop_reason` (refusal) e validar schema; falha = ciclo sem ação
- Notícias entram como campos estruturados (título/resumo/sentimento), nunca texto bruto encadeado — mitigação de prompt injection; a defesa real é o motor de regras
- Revisão semanal: Opus 5 via Batch API (−50%) lê o log da semana e escreve crítica no `learnings/`

### 4.4 Motor de regras (Python puro; a proposta só vira ordem se TODOS passarem)
- **Whitelist dinâmica por critérios** (decisão do dono 2026-09-05: agente com liberdade de escolha dentro de universo protegido): código recalcula semanalmente as ~20 maiores moedas da Binance spot por volume (pares USDT), excluindo stablecoins e tokens alavancados, listadas há ≥1 ano; BTC e ETH sempre incluídas. O LLM escolhe livremente dentro da lista; ticker fora dela = rejeição automática. Primeiro trade em ativo novo exige HITL
- **Sizing por código**: fração fixa do capital modulada pela convicção, teto 10%/ativo, exposição total ≤60%
- Sanidade: preço-alvo ≤ X% do último tick; spread e liquidez do book mínimos; mercado/dado fresco (candle ≤ N min)
- Anti-overtrading: ≤4 ordens/dia, cooldown 4h/ativo, idempotency key por ciclo
- **Circuit breakers**: −5% dia → halt até D+1 · −10% semana → halt+alerta · −15% mês → halt total, retomada só manual · custo de API diário acima do teto → halt · taxa de erro de tools → halt
- **Stop-loss = ordem OCO real na Binance no mesmo instante da entrada** (sobrevive a queda do VPS)
- **HITL**: ordem >2% do capital → Telegram [Aprovar][Rejeitar], TTL 10 min → expira = CANCELA. Também obrigatório: primeiro trade em ativo novo, qualquer ordem pós-circuit-breaker
- **Kill switch 3 níveis** (master/estratégia/instância) via flag fora do processo, acionável por `/kill`; **dead-man switch** (sem heartbeat → halt)
- **Provisão módulo 2**: a unidade de validação é `Estrutura` (spot = estrutura de 1 perna). Interface já comporta os 5 gates de opções (estrutura/allowlist+max_loss finito, liquidez por strike, gregas agregadas, calendário DTE/ex-div/pin, conta/margem)

### 4.5 Execução (único componente com credenciais)
- `binance-connector-python`; chave **sem saque + IP whitelist + Ed25519**; secrets em env vars, nunca em repo/prompt/log
- `newClientOrderId` determinístico; 5XX = estado desconhecido → reconciliar via `GET /openOrders`; reconciliação completa a cada boot; backoff em 429 (418 = ban de IP)
- Interface `VenueAdapter` (get_price, get_book, place_structure, cancel, positions, balances) — Binance é a 1ª implementação; broker de opções EUA será a 2ª

### 4.6 Telegram (bidirecional, único usuário autorizado por chat_id)
- Push: toda ordem/fill/stop, circuit breaker, notícia materialidade ≥4 em posição aberta, digest 9h, resumo semanal
- Comandos: `/status`, `/pausar`, `/retomar`, `/kill`, `/perfil`, `/aprovar|/rejeitar` (e botões inline), chat livre respondido pelo agente (read-only sobre o estado)
- Interface `Notifier`/`CommandSource` — adaptador WhatsApp possível no futuro sem tocar o núcleo

## 5. Fases de entrega (gates, sem pular)

| Fase | Conteúdo | Critério de saída |
|---|---|---|
| 0 | Motor de regras + testes unitários; esqueleto de projeto | 100% dos gates com teste; zero LLM |
| 1 | Ingestão + backtest (vectorbt varredura → backtrader fills realistas), custos 0,10% + slippage modelados | Backtest honesto vs baseline buy-and-hold; ciente de que backtest com LLM em janela antiga é suspeito (look-ahead do treino) |
| 2 | Paper trading: testnet Binance (integração) + dry-run com dados reais (estratégia); Telegram completo; loop Claude ativo | **1–3 meses**; 30 dias consecutivos sem incidente operacional não tratado; motor nunca aprovou ordem que revisão manual rejeitaria |
| 3 | Live micro com **R$ 1.000** (decisão do dono 2026-09-05) | ≥1 mês de métricas estáveis; PnL vs buy-and-hold medido |
| 4 | Escala gradual; ativação opcional do módulo 2 (opções EUA, começando em paper Alpaca) | Só com evidência da fase 3 |

## 6. Orçamento

~US$ 40/mês: VPS €4,50 + Claude API ≈ US$ 35 (24 ciclos/dia, caching TTL 1h) + dados US$ 0 + Telegram US$ 0. Teto diário de gasto de API como circuit breaker. Módulo 2 em live exigirá +US$ 99/mês (feed OPRA da Alpaca).

## 7. Riscos aceitos e mitigação

- **LLMs não têm edge comprovado em previsão de preço** → expectativa: projeto de automação/aprendizado; sucesso = medido contra buy-and-hold após custos; perdas limitadas por código
- **Prompt injection via notícias** → entrada estruturada + motor de regras torna injeção inofensiva
- **Overtrading (patologia nº 1 dos LLMs)** → default "manter", caps de frequência, cooldowns
- **Falha de infra com posição aberta** → stop OCO na exchange + dead-man switch
- **Fiscal**: apuração mensal de cripto (isenção R$35k/mês em exchange nacional; acima, 15–22,5%); o log append-only é a base de cálculo; DeCripto já reporta à RFB
- **Regulatório**: acompanhar regime PSAV (prazo 30/10/2026 p/ exchanges estrangeiras protocolarem no BCB)

## 8. Fora de escopo (YAGNI)

Day trade/alta frequência · derivativos de cripto · alavancagem/margem · B3 (sem via viável; Clear Smart Trader API é day-trade-only) · WhatsApp na v1 · multi-usuário · interface web (Telegram é a UI)

## 9. Referências

Pesquisa completa (6 relatórios, 2026-09-05) em `~/Documents/research/`: `llm-trading-agents-field-report.md`, `brokers-apis-report.md`, `claude-agent-architecture-report.md`, `whatsapp-telegram-datafeeds-report.md`, `videos-report.md`, `options-trading-report.md`.
