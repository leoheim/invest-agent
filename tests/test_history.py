import csv
import io
import urllib.error
import zipfile
from datetime import date

import pytest

from invest_agent.data.history import VISION_URL, fetch_month, month_range


def _zip_bytes(rows: list[list], name: str = "x.csv") -> bytes:
    text = io.StringIO()
    csv.writer(text).writerows(rows)
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as zf:
        zf.writestr(name, text.getvalue())
    return blob.getvalue()


ROW = [1704067200000, "1", "2", "0.5", "1.5", "10", 1704070799999,
       "15", 3, "5", "7.5", "0"]
HEADER = ["open_time", "open", "high", "low", "close", "volume",
          "close_time", "quote_volume", "count", "taker_buy_volume",
          "taker_buy_quote_volume", "ignore"]


def test_month_range_inclusivo_virando_ano():
    assert month_range(date(2023, 11, 5), date(2024, 2, 1)) == [
        (2023, 11), (2023, 12), (2024, 1), (2024, 2)]


def test_fetch_month_parseia_zip_e_monta_url():
    urls = []

    def transport(url: str) -> bytes:
        urls.append(url)
        return _zip_bytes([ROW])

    out = fetch_month("BTCUSDT", "1h", 2024, 1, transport=transport)
    assert len(out) == 1 and out[0].close == 1.5
    assert urls == [VISION_URL.format(symbol="BTCUSDT", interval="1h",
                                      year=2024, month=1)]


def test_fetch_month_ignora_linha_de_header():
    transport = lambda url: _zip_bytes([HEADER, ROW])
    out = fetch_month("BTCUSDT", "1h", 2024, 1, transport=transport)
    assert len(out) == 1


def test_fetch_month_404_devolve_none():
    def transport(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    assert fetch_month("BTCUSDT", "1h", 2016, 1, transport=transport) is None


def test_fetch_month_erro_nao_404_propaga():
    def transport(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 500, "boom", None, None)

    with pytest.raises(urllib.error.HTTPError):
        fetch_month("BTCUSDT", "1h", 2024, 1, transport=transport)
