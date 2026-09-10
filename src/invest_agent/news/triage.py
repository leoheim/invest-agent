"""Triagem por keyword ANTES de qualquer LLM (spec §4.1) — barata,
determinística, e o que ela descarta nunca gasta token. O enriquecimento
LLM (resumo/sentimento/materialidade) é da Fase 2.

Matching: por default, palavras inteiras (\\b...\\b). Exceção: PREFIX_KEYWORDS
que casam deliberadamente como prefixo (ex.: "regulament" casa
"regulamentação"/"regulamento" mas não "regulador" solto)."""
from __future__ import annotations

import re

from .models import NewsItem

ASSET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "BTCUSDT": ("bitcoin", "btc"),
    "ETHUSDT": ("ethereum", "eth", "ether"),
    "SOLUSDT": ("solana", "sol"),
    "BNBUSDT": ("bnb", "binance coin"),
    "XRPUSDT": ("xrp", "ripple"),
}

MACRO_KEYWORDS: tuple[str, ...] = (
    "fed", "juros", "selic", "copom", "inflação", "sec", "etf",
    "regulament", "banco central", "bcb", "halving", "cvm",
)

PREFIX_KEYWORDS: frozenset[str] = frozenset({"regulament"})


def _has_word(word: str, text: str) -> bool:
    if word in PREFIX_KEYWORDS:
        # Prefixais: casam no início da palavra
        return re.search(rf"\b{re.escape(word)}", text) is not None
    else:
        # Não-prefixais: casam palavra inteira
        return re.search(rf"\b{re.escape(word)}\b", text) is not None


def match_assets(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    matched = {symbol
               for symbol, words in ASSET_KEYWORDS.items()
               if any(_has_word(w, lowered) for w in words)}
    return tuple(sorted(matched))


def triage(items: list[NewsItem]) -> list[tuple[NewsItem, tuple[str, ...]]]:
    kept: list[tuple[NewsItem, tuple[str, ...]]] = []
    for item in items:
        text = f"{item.title} {item.summary}"
        assets = match_assets(text)
        is_macro = any(_has_word(w, text.lower()) for w in MACRO_KEYWORDS)
        if assets or is_macro:
            kept.append((item, assets))
    return kept
