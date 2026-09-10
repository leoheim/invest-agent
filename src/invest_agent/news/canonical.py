"""Normalização de URL e SimHash de 64 bits — os dois primeiros estágios
do dedupe da spec §4.1 (URL canônica → SimHash do título). O terceiro
estágio (embedding >0.92) fica para a Fase 2 (exige modelo de embedding)."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"fbclid", "gclid", "ref", "cmpid", "sref"}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith(_TRACKING_PREFIXES)
             and k.lower() not in _TRACKING_PARAMS]
    query.sort()
    path = parts.path
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                       urlencode(query), ""))


def simhash64(text: str) -> int:
    tokens = re.findall(r"\w+", text.lower())
    if not tokens:
        return 0
    weights = [0] * 64
    for token in tokens:
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            weights[bit] += 1 if (value >> bit) & 1 else -1
    return sum(1 << bit for bit in range(64) if weights[bit] > 0)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def is_near_duplicate(a: int, b: int, threshold: int = 3) -> bool:
    return hamming(a, b) <= threshold
