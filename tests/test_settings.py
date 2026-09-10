from pathlib import Path

from invest_agent.settings import Settings


def test_from_env_le_todas_as_vars():
    env = {
        "BINANCE_API_KEY": "k123",
        "BINANCE_API_SECRET": "s456",
        "BINANCE_BASE_URL": "https://api.binance.com",
        "INVEST_DB_PATH": "/tmp/x.db",
        "INVEST_CANDLES_ROOT": "/tmp/candles",
        "INVEST_KILL_PATH": "/tmp/KILL",
        "INVEST_HEARTBEAT_PATH": "/tmp/hb",
        "TELEGRAM_BOT_TOKEN": "t789",
        "TELEGRAM_CHAT_ID": "42",
        "ANTHROPIC_API_KEY": "a000",
        "API_COST_DAILY_CAP_USD": "3.5",
    }
    s = Settings.from_env(env)
    assert s.binance_api_key == "k123" and s.binance_api_secret == "s456"
    assert s.binance_base_url == "https://api.binance.com"
    assert s.db_path == Path("/tmp/x.db")
    assert s.candles_root == Path("/tmp/candles")
    assert s.kill_switch_path == Path("/tmp/KILL")
    assert s.heartbeat_path == Path("/tmp/hb")
    assert s.telegram_token == "t789" and s.telegram_chat_id == "42"
    assert s.anthropic_api_key == "a000"
    assert s.api_cost_daily_cap_usd == 3.5


def test_defaults_apontam_para_testnet_e_data():
    s = Settings.from_env({})
    assert s.binance_base_url == "https://testnet.binance.vision"
    assert s.db_path == Path("data/agent.db")
    assert s.api_cost_daily_cap_usd == 2.0
    assert s.binance_api_key == "" and s.telegram_token == ""


def test_repr_nao_vaza_segredos():
    s = Settings.from_env({"BINANCE_API_KEY": "SEGREDO-K",
                           "BINANCE_API_SECRET": "SEGREDO-S",
                           "TELEGRAM_BOT_TOKEN": "SEGREDO-T",
                           "ANTHROPIC_API_KEY": "SEGREDO-A"})
    texto = repr(s)
    for segredo in ("SEGREDO-K", "SEGREDO-S", "SEGREDO-T", "SEGREDO-A"):
        assert segredo not in texto
