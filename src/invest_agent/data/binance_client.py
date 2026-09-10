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
            except urllib.error.URLError as err:
                if attempt == _RETRIES - 1:
                    raise BinanceError(
                        f"falha de rede em {path} após {_RETRIES} tentativas: {err.reason}") from err
                self._sleep(delay)
                delay *= 2
                continue
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
