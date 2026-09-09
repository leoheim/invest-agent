from invest_agent.breakers import EquityMarks, HaltLevel, check_breakers
from invest_agent.config import MODERADO

MARKS = EquityMarks(day_open=1000.0, week_open=1000.0, month_open=1000.0)


def test_sem_perda_sem_halt():
    assert check_breakers(1000.0, MARKS, MODERADO) is HaltLevel.NONE


def test_perda_diaria_de_5pct_halta_o_dia():
    assert check_breakers(950.0, MARKS, MODERADO) is HaltLevel.DAY


def test_perda_semanal_de_10pct_halta_a_semana():
    marks = EquityMarks(day_open=905.0, week_open=1000.0, month_open=1000.0)
    assert check_breakers(900.0, marks, MODERADO) is HaltLevel.WEEK


def test_retorna_o_nivel_mais_grave():
    # -15% no mês E -5% no dia => MONTH vence
    assert check_breakers(850.0, MARKS, MODERADO) is HaltLevel.MONTH


def test_lucro_nunca_halta():
    assert check_breakers(1200.0, MARKS, MODERADO) is HaltLevel.NONE
