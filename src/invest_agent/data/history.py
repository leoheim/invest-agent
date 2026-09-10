"""Backfill de histórico via data.binance.vision (zips mensais de klines,
grátis, desde 2017 — spec §4.1). Arquivos de 2025+ têm header CSV e
timestamps em microssegundos; Candle.from_kline_row normaliza os µs e o
header é pulado aqui."""
from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
import zipfile
from datetime import date
from typing import Callable

from .models import Candle

VISION_URL = ("https://data.binance.vision/data/spot/monthly/klines/"
              "{symbol}/{interval}/{symbol}-{interval}-{year:04d}-{month:02d}.zip")


def _default_transport(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def month_range(start: date, end: date) -> list[tuple[int, int]]:
    """Todos os (ano, mês) de start a end, inclusivos."""
    out: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def fetch_month(symbol: str, interval: str, year: int, month: int,
                transport: Callable[[str], bytes] | None = None
                ) -> list[Candle] | None:
    """Baixa e parseia um zip mensal. None quando o mês não existe (404) —
    ex.: símbolo ainda não listado naquele mês."""
    transport = transport or _default_transport
    url = VISION_URL.format(symbol=symbol, interval=interval,
                            year=year, month=month)
    try:
        blob = transport(url)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        text = zf.read(zf.namelist()[0]).decode()
    out: list[Candle] = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip().isdigit():
            continue  # linha vazia ou header (arquivos 2025+)
        out.append(Candle.from_kline_row(symbol, interval, row))
    return out
