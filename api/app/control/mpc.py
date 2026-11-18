"""Model predictive control as a mixed-integer linear program (MILP).

Structure following Ruddick et al. (2024), "Real-world validation of safe reinforcement learning, model
predictive control and decision tree-based home energy management systems", Energy and AI,
section 3.5.3:

  Objective (eq. 12)       min  Σ_t (E_o,t·V_o,t − E_i,t·V_i,t)  +  V_p · max_t(P_o,t, P_p)
  Battery model (eq. 11)   SOC_t+1 = SOC_t + (η·P_ch,t − P_di,t/η)·Δt
  Power limits (9b/9d)     0 ≤ P_ch, P_di ≤ P_max,  0 ≤ P_grid ≤ P_grid,max
  Balance (10a)            P_import − P_export = load − PV + P_ch − P_di
  Binary variable (10c)    no simultaneous charging and discharging
  Terminal condition       SOC at the end of the horizon ≥ target (soft: shortfall costs 100 ct/kWh)

Differences to the paper: the import price V_o is the dynamic EKZ tariff (incl. grid and
levies, hence V_f is dropped), the feed-in price V_i is the day-ahead price or a fixed tariff.
Additionally there are optional cycle costs (wear per kWh throughput), PV curtailment and
a small tie-breaker that prefers early storage at equal cost (robust against
PV forecast errors).
An EV charging point is not part of this calculator.

Solved with HiGHS via scipy.optimize.milp.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix, csr_matrix

TERMINAL_PENALTY = 100.0  # ct per kWh below the target SOC
# Tie-breaker: at equal cost, prefer to store energy earlier (ct per kWh in storage and hour).
# Without it, the optimisation often does not care whether PV surplus is charged at midday or only
# in the evening – if PV then turns out weaker than forecast, the energy is missing.
HOLD_BONUS = 0.05


@dataclass
class Battery:
    capacity_kwh: float
    power_kw: float
    efficiency: float = 0.95
    soc_min: float = 0.1  # share of capacity
    soc_max: float = 1.0
    cycle_cost: float = 0.0  # ct/kWh throughput (charging + discharging)


@dataclass
class Grid:
    import_kw: float = 11.0
    export_kw: float = 11.0
    allow_grid_charging: bool = True  # battery may charge from the grid
    allow_battery_export: bool = True  # battery may feed into the grid
    peak_price: float = 0.0  # ct per kW import peak in the horizon (V_p in the paper)
    previous_peak_kw: float = 0.0  # P_p in the paper


@dataclass
class Plan:
    charge: np.ndarray  # kW
    discharge: np.ndarray  # kW
    grid_import: np.ndarray  # kW
    grid_export: np.ndarray  # kW
    pv_used: np.ndarray  # kW (after curtailment)
    soc: np.ndarray  # kWh, length N+1
    objective: float  # ct
    status: str


def solve(
    pv: np.ndarray,
    load: np.ndarray,
    price_import: np.ndarray,
    price_export: np.ndarray,
    soc0: float,
    soc_terminal: float,
    battery: Battery,
    grid: Grid,
    dt: float = 0.25,
    terminal_value: tuple[float, float] | None = None,
    hold_bonus: float = HOLD_BONUS,
) -> Plan:
    """Optimal schedule over the horizon (all quantities per time step, prices in ct/kWh).

    Without `terminal_value` the (soft) terminal condition SOC_N ≥ soc_terminal applies as in the paper.
    With `terminal_value = (credit, cost)` in ct/kWh the deviation from the target is valued instead:
    more energy at the end is credited, missing energy costs – just like in the
    billing of the backtest.
    """
    n = len(pv)
    # Variable blocks: pc, pd, gi, ge, pvu, b (binary), peak (1), soc (n+1), slack terminal condition (1)
    PC, PD, GI, GE, PVU, B = (i * n for i in range(6))
    PEAK = 6 * n
    SOC = 6 * n + 1
    SLACK = SOC + n + 1
    UP, DOWN = SLACK + 1, SLACK + 2  # deviation from the target SOC (only with terminal_value)
    nv = SLACK + 3

    eta = battery.efficiency
    cap = battery.capacity_kwh
    pmax = battery.power_kw

    c = np.zeros(nv)
    c[GI:GI + n] = price_import * dt
    c[GE:GE + n] = -price_export * dt
    c[PC:PC + n] = battery.cycle_cost * dt
    c[PD:PD + n] = battery.cycle_cost * dt
    c[PEAK] = grid.peak_price
    c[SOC + 1:SOC + n + 1] = -hold_bonus * dt
    if terminal_value is None:
        c[SLACK] = TERMINAL_PENALTY
    else:
        c[UP] = -terminal_value[0]
        c[DOWN] = terminal_value[1]

    lb = np.zeros(nv)
    ub = np.full(nv, np.inf)
    ub[PC:PC + n] = pmax
    ub[PD:PD + n] = pmax
    ub[GI:GI + n] = grid.import_kw
    ub[GE:GE + n] = grid.export_kw
    ub[PVU:PVU + n] = np.maximum(pv, 0)
    ub[B:B + n] = 1
    lb[PEAK] = grid.previous_peak_kw
    lb[SOC:SOC + n + 1] = battery.soc_min * cap
    ub[SOC:SOC + n + 1] = battery.soc_max * cap
    soc0 = float(np.clip(soc0, battery.soc_min * cap, battery.soc_max * cap))
    lb[SOC] = ub[SOC] = soc0
    target = float(np.clip(soc_terminal, battery.soc_min * cap, battery.soc_max * cap))

    if not grid.allow_grid_charging:
        # Grid import only for consumption – so charging only from PV
        ub[GI:GI + n] = np.minimum(grid.import_kw, np.maximum(load, 0))

    rows, cols, vals, lo, hi = [], [], [], [], []
    r = 0

    def add(entries: list[tuple[int, float]], low: float, high: float) -> None:
        nonlocal r
        for col, val in entries:
            rows.append(r)
            cols.append(col)
            vals.append(val)
        lo.append(low)
        hi.append(high)
        r += 1

    if terminal_value is None:
        # Terminal condition: SOC_N + slack ≥ target
        add([(SOC + n, 1), (SLACK, 1)], target, np.inf)
    else:
        # SOC_N − surplus + shortfall = target
        add([(SOC + n, 1), (UP, -1), (DOWN, 1)], target, target)

    for t in range(n):
        # Balance (10a): gi − ge − pc + pd + pvu = load
        add([(GI + t, 1), (GE + t, -1), (PC + t, -1), (PD + t, 1), (PVU + t, 1)], load[t], load[t])
        # Battery model (11)
        add([(SOC + t + 1, 1), (SOC + t, -1), (PC + t, -eta * dt), (PD + t, dt / eta)], 0, 0)
        # Binary (10c): pc ≤ Pmax·b,  pd ≤ Pmax·(1 − b)
        add([(PC + t, 1), (B + t, -pmax)], -np.inf, 0)
        add([(PD + t, 1), (B + t, pmax)], -np.inf, pmax)
        # Peak load: peak ≥ gi
        if grid.peak_price > 0:
            add([(PEAK, 1), (GI + t, -1)], 0, np.inf)
        if not grid.allow_battery_export:
            # Discharge at most as much as is consumed in the house
            add([(PD + t, 1)], -np.inf, max(load[t], 0))

    a = csr_matrix(coo_matrix((vals, (rows, cols)), shape=(r, nv)))
    integrality = np.zeros(nv)
    integrality[B:B + n] = 1

    res = milp(
        c,
        constraints=LinearConstraint(a, lo, hi),
        integrality=integrality,
        bounds=Bounds(lb, ub),
        options={"time_limit": 10, "mip_rel_gap": 1e-4},
    )
    if res.x is None:
        raise RuntimeError(f"MPC not solvable: {res.message}")

    x = res.x
    return Plan(
        charge=x[PC:PC + n],
        discharge=x[PD:PD + n],
        grid_import=x[GI:GI + n],
        grid_export=x[GE:GE + n],
        pv_used=x[PVU:PVU + n],
        soc=x[SOC:SOC + n + 1],
        objective=float(res.fun),
        status=res.message,
    )
