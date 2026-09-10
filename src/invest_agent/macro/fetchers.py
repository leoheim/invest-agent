"""Fetchers macro da spec §4.1 (1×/dia): Fear & Greed (alternative.me) e
BCB SGS (Selic 432, câmbio 1). Transport injetável — nenhum teste toca a
rede; o timestamp do próprio dado (não o relógio local) define a data."""
from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timezone
from typing import Callable

from ..storage.sqlite_store import MacroPoint

FNG_URL = "https://api.alternative.me/fng/?limit=1"
SGS_URL = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_id}"
           "/dados/ultimos/1?formato=json")

SGS_SELIC = 432
SGS_CAMBIO = 1


def _default_transport(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def fetch_fear_greed(
        transport: Callable[[str], bytes] | None = None) -> MacroPoint:
    transport = transport or _default_transport
    payload = json.loads(transport(FNG_URL))
    entry = payload["data"][0]
    when = datetime.fromtimestamp(int(entry["timestamp"]),
                                  tz=timezone.utc).date()
    return MacroPoint("fng", when, float(entry["value"]))


def fetch_bcb_sgs(series_id: int, series_name: str,
                  transport: Callable[[str], bytes] | None = None
                  ) -> MacroPoint:
    transport = transport or _default_transport
    payload = json.loads(transport(SGS_URL.format(series_id=series_id)))
    entry = payload[-1]
    day, month, year = entry["data"].split("/")
    value = float(entry["valor"].replace(",", "."))
    return MacroPoint(series_name, date(int(year), int(month), int(day)),
                      value)
