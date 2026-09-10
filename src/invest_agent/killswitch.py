"""Kill switch = arquivo em disco. Matar o processo não apaga a flag;
o supervisor externo e o bot do Telegram só precisam de filesystem.
Heartbeat falha FECHADO: sem arquivo = stale = halt."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


class KillSwitch:
    def __init__(self, path: Path) -> None:
        self.path = path

    def activate(self, reason: str) -> None:
        self.path.write_text(reason, encoding="utf-8")

    def deactivate(self) -> None:
        self.path.unlink(missing_ok=True)

    def is_active(self) -> bool:
        return self.path.exists()

    def reason(self) -> str | None:
        if not self.is_active():
            return None
        return self.path.read_text(encoding="utf-8")


def heartbeat_beat(path: Path, now: datetime) -> None:
    path.write_text(now.isoformat(), encoding="utf-8")


def heartbeat_stale(path: Path, max_age_seconds: float, now: datetime) -> bool:
    if not path.exists():
        return True
    try:
        last = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return True
    return (now - last).total_seconds() > max_age_seconds
