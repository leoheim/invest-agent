from datetime import date

from invest_agent.macro.fetchers import (
    SGS_CAMBIO, SGS_SELIC, fetch_bcb_sgs, fetch_fear_greed,
)
from invest_agent.storage.sqlite_store import MacroPoint

FNG = b'{"data":[{"value":"34","value_classification":"Fear","timestamp":"1788912000"}]}'
SGS = b'[{"data":"09/09/2026","valor":"15,00"}]'
SGS_PONTO = b'[{"data":"09/09/2026","valor":"5.4321"}]'


def test_fetch_fear_greed():
    urls = []

    def transport(url):
        urls.append(url)
        return FNG

    point = fetch_fear_greed(transport)
    assert point == MacroPoint("fng", date(2026, 9, 9), 34.0)
    assert "alternative.me/fng" in urls[0]


def test_fetch_bcb_sgs_virgula_decimal():
    urls = []

    def transport(url):
        urls.append(url)
        return SGS

    point = fetch_bcb_sgs(SGS_SELIC, "selic", transport)
    assert point == MacroPoint("selic", date(2026, 9, 9), 15.0)
    assert "bcdata.sgs.432" in urls[0] and "ultimos/1" in urls[0]


def test_fetch_bcb_sgs_ponto_decimal():
    point = fetch_bcb_sgs(SGS_CAMBIO, "cambio", lambda u: SGS_PONTO)
    assert point == MacroPoint("cambio", date(2026, 9, 9), 5.4321)


def test_constantes_da_spec():
    assert SGS_SELIC == 432 and SGS_CAMBIO == 1
