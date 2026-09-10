"""Estado operacional do ciclo: carteira MARCADA A MERCADO (fecha o débito
documentado da Fase 0 — o engine continua igual, mas quem o alimenta agora
usa preços atuais), rolagem dos marks de equity (dia/semana/mês UTC) e o
halt persistente com regras de release da spec §4.4: DAY até D+1, WEEK até
a segunda seguinte, MONTH só com retomada manual do dono."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..breakers import EquityMarks, HaltLevel
from ..models import PortfolioState, Position
from ..storage.sqlite_store import SqliteStore


def _base_asset(symbol: str) -> str:
    return symbol.removesuffix("USDT")


def build_portfolio(store: SqliteStore, balances: dict[str, float],
                    prices: dict[str, float], now: datetime) -> PortfolioState:
    cash = balances.get("USDT", 0.0)
    positions: dict[str, Position] = {}
    for symbol, (_, avg_price, _) in store.get_positions().items():
        qty = balances.get(_base_asset(symbol), 0.0)
        if qty <= 0:
            store.delete_position(symbol)  # a exchange é a verdade
            continue
        positions[symbol] = Position(symbol=symbol, qty=qty,
                                     avg_price=avg_price)
    equity = cash + sum(p.qty * prices.get(p.symbol, p.avg_price)
                        for p in positions.values())
    return PortfolioState(
        equity=equity,
        cash=cash,
        positions=positions,
        orders_today=store.count_orders_on(now.date()),
        last_order_at=store.last_order_at_by_symbol(),
    )


def _monday(dt: datetime) -> datetime:
    day = dt.date() - timedelta(days=dt.weekday())
    return datetime(day.year, day.month, day.day, tzinfo=dt.tzinfo)


def _rolled(period: str, opened_at: datetime, now: datetime) -> bool:
    if period == "day":
        return opened_at.date() != now.date()
    if period == "week":
        return _monday(opened_at) != _monday(now)
    return (opened_at.year, opened_at.month) != (now.year, now.month)


def ensure_marks(store: SqliteStore, equity: float,
                 now: datetime) -> EquityMarks:
    values: dict[str, float] = {}
    for period in ("day", "week", "month"):
        mark = store.get_mark(period)
        if mark is None or _rolled(period, mark[1], now):
            store.set_mark(period, equity, now)
            values[period] = equity
        else:
            values[period] = mark[0]
    return EquityMarks(day_open=values["day"], week_open=values["week"],
                       month_open=values["month"])


_SEVERITY = {HaltLevel.NONE: 0, HaltLevel.DAY: 1,
             HaltLevel.WEEK: 2, HaltLevel.MONTH: 3}


def record_halt_if_needed(store: SqliteStore, level: HaltLevel,
                          now: datetime) -> None:
    if level is HaltLevel.NONE:
        return
    current = store.get_halt()
    if current is not None and not current[2]:
        if _SEVERITY[HaltLevel[current[0]]] >= _SEVERITY[level]:
            return
    store.set_halt(level.name, now)


def active_halt(store: SqliteStore, now: datetime) -> HaltLevel:
    row = store.get_halt()
    if row is None:
        return HaltLevel.NONE
    level_name, set_at, released = row
    if released:
        return HaltLevel.NONE
    level = HaltLevel[level_name]
    if level is HaltLevel.DAY and now.date() > set_at.date():
        return HaltLevel.NONE
    if level is HaltLevel.WEEK and _monday(now) > _monday(set_at):
        return HaltLevel.NONE
    return level
