import numpy as np

from app.control.mpc import Battery, Grid, solve

N = 96
T = np.arange(N)
PV = np.clip(6 * np.sin((T - 28) / 48 * np.pi), 0, None)
LOAD = np.full(N, 0.6)
PRICE = 20 + 8 * np.sin((T - 60) / 96 * 2 * np.pi)
EXPORT = np.full(N, 8.0)


def test_energy_balance_and_soc_model():
    bat = Battery(10, 5)
    plan = solve(PV, LOAD, PRICE, EXPORT, 5, 5, bat, Grid())
    balance = plan.grid_import - plan.grid_export - plan.charge + plan.discharge + plan.pv_used
    assert np.allclose(balance, LOAD, atol=1e-6)
    soc = 5 + np.cumsum((plan.charge * bat.efficiency - plan.discharge / bat.efficiency) * 0.25)
    assert np.allclose(plan.soc[1:], soc, atol=1e-6)
    assert plan.soc[-1] >= 5 - 1e-6


def test_no_simultaneous_charge_and_discharge():
    plan = solve(PV, LOAD, PRICE, np.full(N, -5.0), 5, 5, Battery(10, 5), Grid())
    assert np.all(np.minimum(plan.charge, plan.discharge) < 1e-6)


def test_battery_lowers_cost():
    without = solve(PV, LOAD, PRICE, EXPORT, 0, 0, Battery(0, 0), Grid())
    with_battery = solve(PV, LOAD, PRICE, EXPORT, 5, 5, Battery(10, 5), Grid())
    assert with_battery.objective < without.objective


def test_no_grid_charging_respected():
    plan = solve(np.zeros(N), LOAD, PRICE, EXPORT, 1, 8, Battery(10, 5), Grid(allow_grid_charging=False))
    assert np.all(plan.grid_import <= LOAD + 1e-6)
    assert plan.charge.sum() < 1e-6  # ohne PV kann nichts geladen werden – Endziel weich verfehlt


def test_terminal_value_rewards_extra_energy():
    # Hohe Gutschrift für Energie am Ende → Batterie wird voll geladen
    plan = solve(PV, LOAD, PRICE, EXPORT, 5, 5, Battery(10, 5), Grid(), terminal_value=(100.0, 200.0))
    assert plan.soc[-1] > 9.9
