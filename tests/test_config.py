from invest_agent.config import MODERADO


def test_perfil_moderado_bate_com_a_spec():
    assert MODERADO.max_position_pct == 0.10
    assert MODERADO.max_exposure_pct == 0.60
    assert MODERADO.daily_loss_halt_pct == 0.05
    assert MODERADO.weekly_loss_halt_pct == 0.10
    assert MODERADO.monthly_loss_halt_pct == 0.15
    assert MODERADO.max_orders_per_day == 4
    assert MODERADO.cooldown_hours == 4.0
    assert MODERADO.hitl_threshold_pct == 0.02
    assert MODERADO.stop_loss_pct > 0  # stop obrigatório, valor > 0
