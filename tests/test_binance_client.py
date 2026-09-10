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


def test_urlerror_uma_vez_retenta():
    calls, sleeps = [], []

    def transport(url: str) -> bytes:
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.URLError("connection reset")
        return b"[]"

    client = BinanceMarketData(transport=transport, sleeper=sleeps.append)
    assert client.klines("BTCUSDT", "1h") == []
    assert len(calls) == 2 and len(sleeps) == 1


def test_urlerror_persistente_vira_binance_error():
    def transport(url: str) -> bytes:
        raise urllib.error.URLError("connection refused")

    client = BinanceMarketData(transport=transport, sleeper=lambda s: None)
    with pytest.raises(BinanceError):
        client.klines("BTCUSDT", "1h")
