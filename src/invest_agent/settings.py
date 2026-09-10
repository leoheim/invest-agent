"""Configuração por variáveis de ambiente (spec §4.5: secrets em env,
nunca em repo/prompt/log). from_env recebe o mapping — só o main() de um
CLI passa os.environ de verdade. Campos secretos têm repr=False: um
traceback ou log de Settings nunca vaza chave."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    binance_api_key: str = field(default="", repr=False)
    binance_api_secret: str = field(default="", repr=False)
    binance_base_url: str = "https://testnet.binance.vision"
    db_path: Path = Path("data/agent.db")
    candles_root: Path = Path("data/candles")
    kill_switch_path: Path = Path("data/KILL")
    heartbeat_path: Path = Path("data/heartbeat")
    telegram_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""
    anthropic_api_key: str = field(default="", repr=False)
    api_cost_daily_cap_usd: float = 2.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        base = cls()
        return cls(
            binance_api_key=env.get("BINANCE_API_KEY", ""),
            binance_api_secret=env.get("BINANCE_API_SECRET", ""),
            binance_base_url=env.get("BINANCE_BASE_URL", base.binance_base_url),
            db_path=Path(env.get("INVEST_DB_PATH", str(base.db_path))),
            candles_root=Path(env.get("INVEST_CANDLES_ROOT",
                                      str(base.candles_root))),
            kill_switch_path=Path(env.get("INVEST_KILL_PATH",
                                          str(base.kill_switch_path))),
            heartbeat_path=Path(env.get("INVEST_HEARTBEAT_PATH",
                                        str(base.heartbeat_path))),
            telegram_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID", ""),
            anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
            api_cost_daily_cap_usd=float(
                env.get("API_COST_DAILY_CAP_USD",
                        str(base.api_cost_daily_cap_usd))),
        )
