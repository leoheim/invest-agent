"""O gate central: a ÚNICA porta entre uma proposta do LLM e uma ordem.
Toda regra é acumulativa — o Verdict rejeitado carrega TODOS os motivos,
para o dono ver o quadro completo no Telegram.

Nota sobre total_invested_notional em _build_buy():
Avalia as demais posições a avg_price (custo histórico), não a preço de
mercado. Quando posições se valorizaram desde a entrada, isto SUBESTIMA o
total investido, podendo aprovar uma compra que faz a exposição real
(mark-to-market) ultrapassar silenciosamente o teto de 60%. O erro é
unidirecional e permissivo em exposição. Em Fase 0 isto é aceitável pois o
agent não rebalanceia, apenas abre/fecha posições. Em produção (Fase 1+),
o orquestrador com market data completo recalculará a exposição antes de
enviar à exchange."""
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

        if market.symbol != proposal.symbol:
            raise ValueError(f"snapshot de mercado de {market.symbol} não corresponde à proposta de {proposal.symbol}")

        reasons: list[str] = []

        if self.kill_switch.is_active():
            reasons.append(f"kill switch ativo: {self.kill_switch.reason()}")

        halt = check_breakers(portfolio.equity, marks, self.profile)
        if halt is not HaltLevel.NONE:
            reasons.append(f"circuit breaker acionado (nível {halt.name})")

        # Whitelist governa entradas (BUY); posição existente deve sempre ser fechável
        if proposal.action is Action.BUY and proposal.symbol not in self.whitelist:
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
