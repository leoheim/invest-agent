from invest_agent.whitelist import SymbolStats, build_whitelist, ALWAYS_INCLUDED


def _s(symbol, base, vol, days=400, lev=False, quote="USDT"):
    return SymbolStats(symbol=symbol, base=base, quote=quote,
                       quote_volume_30d=vol, listed_days=days,
                       is_leveraged=lev)


def test_btc_e_eth_sempre_entram_mesmo_sem_stats():
    assert ALWAYS_INCLUDED <= build_whitelist([])


def test_seleciona_top_por_volume():
    stats = [_s("SOLUSDT", "SOL", 900), _s("XRPUSDT", "XRP", 800),
             _s("DOGEUSDT", "DOGE", 100)]
    wl = build_whitelist(stats, size=2)
    assert "SOLUSDT" in wl and "XRPUSDT" in wl
    assert "DOGEUSDT" not in wl


def test_exclui_stablecoins_alavancados_novos_e_nao_usdt():
    stats = [
        _s("USDCUSDT", "USDC", 9999),            # stablecoin
        _s("BTCUPUSDT", "BTCUP", 9999, lev=True), # token alavancado
        _s("NEWUSDT", "NEW", 9999, days=30),      # listado há <1 ano
        _s("SOLBRL", "SOL", 9999, quote="BRL"),   # par não-USDT
        _s("SOLUSDT", "SOL", 500),
    ]
    wl = build_whitelist(stats, size=20)
    assert wl - ALWAYS_INCLUDED == {"SOLUSDT"}
