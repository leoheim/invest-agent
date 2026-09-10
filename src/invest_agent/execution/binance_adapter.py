"""Adapter spot da Binance (testnet por default) — o ÚNICO componente que
verá credenciais (spec §4.5). HMAC SHA256; POSTs de ordem NUNCA re-tentam
(5xx = estado desconhecido → reconciliar via get_order); GETs re-tentam.
newClientOrderId determinístico vem do motor (idempotência)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from ..models import OrderIntent

_GET_RETRIES = 3


class BinanceAdapterError(Exception):
    """Falha na comunicação/execução com a Binance."""


def _default_http(method: str, url: str, headers: dict,
                  body: bytes | None) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _fmt(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


class BinanceSpotAdapter:
    def __init__(self, api_key: str, api_secret: str, base_url: str,
                 http: Callable[[str, str, dict, bytes | None], bytes] | None = None,
                 clock_ms: Callable[[], int] | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self._key = api_key
        self._secret = api_secret.encode()
        self._base = base_url.rstrip("/")
        self._http = http or _default_http
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleeper

    # --- núcleo de request ---

    def _request(self, method: str, path: str, params: dict,
                 signed: bool, retry: bool) -> dict:
        params = dict(params)
        headers: dict = {}
        if signed:
            params["timestamp"] = str(self._clock_ms())
            params["recvWindow"] = "5000"
            query = urllib.parse.urlencode(params)
            signature = hmac.new(self._secret, query.encode(),
                                 hashlib.sha256).hexdigest()
            query = f"{query}&signature={signature}"
            headers["X-MBX-APIKEY"] = self._key
        else:
            query = urllib.parse.urlencode(params)
        url = f"{self._base}{path}" + (f"?{query}" if query else "")
        attempts = _GET_RETRIES if retry else 1
        delay = 1.0
        for attempt in range(attempts):
            try:
                return json.loads(self._http(method, url, headers, None))
            except urllib.error.HTTPError as err:
                if err.code == 418:
                    raise BinanceAdapterError(
                        f"IP banido pela Binance (418) em {path}") from err
                transient = err.code == 429 or err.code >= 500
                if transient and retry and attempt < attempts - 1:
                    self._sleep(delay)
                    delay *= 2
                    continue
                if transient and not retry:
                    raise BinanceAdapterError(
                        f"HTTP {err.code} em {method} {path} — estado "
                        "desconhecido; reconcilie com get_order") from err
                raise BinanceAdapterError(
                    f"HTTP {err.code} em {method} {path}") from err
            except urllib.error.URLError as err:
                if retry and attempt < attempts - 1:
                    self._sleep(delay)
                    delay *= 2
                    continue
                if not retry:
                    raise BinanceAdapterError(
                        f"falha de rede em {method} {path} — estado "
                        "desconhecido; reconcilie com get_order") from err
                raise BinanceAdapterError(
                    f"falha de rede em {method} {path}") from err
        raise AssertionError("inalcançável")

    # --- mercado (público) ---

    def get_price(self, symbol: str) -> float:
        data = self._request("GET", "/api/v3/ticker/price",
                             {"symbol": symbol}, signed=False, retry=True)
        return float(data["price"])

    def get_book(self, symbol: str) -> tuple[float, float]:
        data = self._request("GET", "/api/v3/ticker/bookTicker",
                             {"symbol": symbol}, signed=False, retry=True)
        return float(data["bidPrice"]), float(data["askPrice"])

    # --- conta (assinado) ---

    def get_balances(self) -> dict[str, tuple[float, float]]:
        """{asset: (free, locked)} — um STOP_LOSS_LIMIT GTC move o ativo
        base para `locked`; se olhássemos só `free`, build_portfolio
        acharia a posição zerada assim que o stop de entrada fosse
        colocado (C1)."""
        data = self._request("GET", "/api/v3/account", {},
                             signed=True, retry=True)
        out: dict[str, tuple[float, float]] = {}
        for b in data.get("balances", []):
            free, locked = float(b["free"]), float(b["locked"])
            if free + locked > 0:
                out[b["asset"]] = (free, locked)
        return out

    # --- ordens (assinado; SEM retry) ---

    def place_limit_ioc(self, order: OrderIntent) -> dict:
        return self._request("POST", "/api/v3/order", {
            "symbol": order.symbol,
            "side": order.side,
            "type": "LIMIT",
            "timeInForce": "IOC",
            "quantity": _fmt(order.qty),
            "price": _fmt(order.limit_price),
            "newClientOrderId": order.client_order_id,
        }, signed=True, retry=False)

    def place_stop_loss(self, symbol: str, qty: float, stop_price: float,
                        client_order_id: str) -> dict:
        return self._request("POST", "/api/v3/order", {
            "symbol": symbol,
            "side": "SELL",
            "type": "STOP_LOSS_LIMIT",
            "timeInForce": "GTC",
            "quantity": _fmt(qty),
            "stopPrice": _fmt(stop_price),
            "price": _fmt(stop_price * 0.995),
            "newClientOrderId": client_order_id,
        }, signed=True, retry=False)

    def cancel_order(self, symbol: str, client_order_id: str) -> dict:
        return self._request("DELETE", "/api/v3/order", {
            "symbol": symbol,
            "origClientOrderId": client_order_id,
        }, signed=True, retry=False)

    def get_order(self, symbol: str, client_order_id: str) -> dict:
        return self._request("GET", "/api/v3/order", {
            "symbol": symbol,
            "origClientOrderId": client_order_id,
        }, signed=True, retry=True)
