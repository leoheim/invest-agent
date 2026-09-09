from datetime import datetime, timedelta, timezone

from invest_agent.killswitch import KillSwitch, heartbeat_beat, heartbeat_stale

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def test_killswitch_ativa_desativa(tmp_path):
    ks = KillSwitch(tmp_path / "KILL")
    assert not ks.is_active()
    ks.activate("comando /kill do dono")
    assert ks.is_active()
    assert ks.reason() == "comando /kill do dono"
    ks.deactivate()
    assert not ks.is_active()


def test_heartbeat_fresco_nao_esta_stale(tmp_path):
    hb = tmp_path / "heartbeat"
    heartbeat_beat(hb, NOW)
    assert not heartbeat_stale(hb, max_age_seconds=300, now=NOW + timedelta(seconds=60))


def test_heartbeat_velho_esta_stale(tmp_path):
    hb = tmp_path / "heartbeat"
    heartbeat_beat(hb, NOW)
    assert heartbeat_stale(hb, max_age_seconds=300, now=NOW + timedelta(seconds=301))


def test_heartbeat_inexistente_falha_fechado(tmp_path):
    assert heartbeat_stale(tmp_path / "nao-existe", max_age_seconds=300, now=NOW)
