import hashlib
import hmac
import json
import urllib.error
import urllib.parse

import pytest

from invest_agent.execution.binance_adapter import (
    BinanceAdapterError, BinanceSpotAdapter,
)
from invest_agent.models import OrderIntent

KEY, SECRET = "k-test", "s-test"
CLOCK = lambda: 1_789_000_000_000  # ms fixo


def _adapter(http):
    return BinanceSpotAdapter(KEY, SECRET, "https://testnet.binance.vision",
                              http=http, clock_ms=CLOCK,
                              sleeper=lambda s: None)


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", None, None)


def test_get_price_publico_sem_assinatura():
    calls = []

    def http(method, url, headers, body):
        calls.append((method, url, headers))
        return json.dumps({"symbol": "BTCUSDT", "price": "50000.10"}).encode()

    assert _adapter(http).get_price("BTCUSDT") == 50000.10
    method, url, headers = calls[0]
    assert method == "GET" and "signature" not in url
    assert "X-MBX-APIKEY" not in headers


def test_get_balances_assina_e_filtra_zerados():
    def http(method, url, headers, body):
        query = urllib.parse.urlsplit(url).query
        params = dict(urllib.parse.parse_qsl(query))
        base = query.rsplit("&signature=", 1)[0]
        esperada = hmac.new(SECRET.encode(), base.encode(),
                            hashlib.sha256).hexdigest()
        assert params["signature"] == esperada
        assert params["timestamp"] == "1789000000000"
        assert headers["X-MBX-APIKEY"] == KEY
        return json.dumps({"balances": [
            {"asset": "USDT", "free": "1000.5", "locked": "0"},
            {"asset": "BTC", "free": "0.25", "locked": "0"},
            {"asset": "ETH", "free": "0.00000000", "locked": "0"},
        ]}).encode()

    balances = _adapter(http).get_balances()
    assert balances == {"USDT": (1000.5, 0.0), "BTC": (0.25, 0.0)}


def test_get_balances_inclui_ativo_todo_travado_num_stop():
    # C1: um STOP_LOSS_LIMIT GTC move o saldo de free para locked; um
    # ativo com free=0 e locked>0 (posição travada no stop) não pode ser
    # filtrado, senão build_portfolio acha a posição zerada.
    def http(method, url, headers, body):
        return json.dumps({"balances": [
            {"asset": "USDT", "free": "500.0", "locked": "0"},
            {"asset": "BTC", "free": "0.00000000", "locked": "0.5"},
        ]}).encode()

    balances = _adapter(http).get_balances()
    assert balances == {"USDT": (500.0, 0.0), "BTC": (0.0, 0.5)}


def test_place_limit_ioc_monta_ordem():
    calls = []

    def http(method, url, headers, body):
        calls.append((method, url))
        return json.dumps({"status": "FILLED", "executedQty": "0.1",
                           "cummulativeQuoteQty": "5000"}).encode()

    order = OrderIntent(symbol="BTCUSDT", side="BUY", qty=0.1,
                        limit_price=50_000.0, stop_loss_price=47_500.0,
                        client_order_id="ia-abc")
    resp = _adapter(http).place_limit_ioc(order)
    assert resp["status"] == "FILLED"
    method, url = calls[0]
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert method == "POST"
    assert params["type"] == "LIMIT" and params["timeInForce"] == "IOC"
    assert params["side"] == "BUY" and params["symbol"] == "BTCUSDT"
    assert params["quantity"] == "0.1" and params["price"] == "50000"
    assert params["newClientOrderId"] == "ia-abc"


def test_post_nao_retenta_e_manda_reconciliar():
    tentativas = []

    def http(method, url, headers, body):
        tentativas.append(1)
        raise _http_error(500)

    order = OrderIntent(symbol="BTCUSDT", side="BUY", qty=0.1,
                        limit_price=50_000.0, stop_loss_price=None,
                        client_order_id="ia-abc")
    with pytest.raises(BinanceAdapterError, match="reconcilie"):
        _adapter(http).place_limit_ioc(order)
    assert len(tentativas) == 1  # NUNCA re-tenta POST


def test_timeout_de_leitura_tambem_manda_reconciliar():
    # TimeoutError não é URLError (urlopen não o embrulha), então precisa
    # do próprio handler — senão escapa cru e perde a dica de reconciliar.
    tentativas = []

    def http(method, url, headers, body):
        tentativas.append(1)
        raise TimeoutError("timed out")

    order = OrderIntent(symbol="BTCUSDT", side="BUY", qty=0.1,
                        limit_price=50_000.0, stop_loss_price=None,
                        client_order_id="ia-abc")
    with pytest.raises(BinanceAdapterError, match="reconcilie"):
        _adapter(http).place_limit_ioc(order)
    assert len(tentativas) == 1  # POST não re-tenta nem em timeout


def test_get_retenta_5xx_e_depois_sucede():
    tentativas = []

    def http(method, url, headers, body):
        tentativas.append(1)
        if len(tentativas) < 3:
            raise _http_error(500)
        return json.dumps({"symbol": "BTCUSDT", "price": "1"}).encode()

    assert _adapter(http).get_price("BTCUSDT") == 1.0
    assert len(tentativas) == 3


def test_418_erro_imediato():
    def http(method, url, headers, body):
        raise _http_error(418)

    with pytest.raises(BinanceAdapterError, match="banido"):
        _adapter(http).get_price("BTCUSDT")


def test_place_stop_loss_e_cancel():
    calls = []

    def http(method, url, headers, body):
        calls.append((method, url))
        return b"{}"

    adapter = _adapter(http)
    adapter.place_stop_loss("BTCUSDT", 0.1, 47_500.0, "ia-abc-sl")
    adapter.cancel_order("BTCUSDT", "ia-abc-sl")
    params_stop = dict(urllib.parse.parse_qsl(
        urllib.parse.urlsplit(calls[0][1]).query))
    assert params_stop["type"] == "STOP_LOSS_LIMIT"
    assert params_stop["stopPrice"] == "47500"
    assert params_stop["price"] == "47262.5"  # 47500 * 0.995
    assert params_stop["timeInForce"] == "GTC"
    assert calls[1][0] == "DELETE"
    params_cancel = dict(urllib.parse.parse_qsl(
        urllib.parse.urlsplit(calls[1][1]).query))
    assert params_cancel["origClientOrderId"] == "ia-abc-sl"
