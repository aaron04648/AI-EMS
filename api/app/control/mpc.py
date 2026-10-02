"""Model Predictive Control als gemischt-ganzzahliges lineares Programm (MILP).

Aufbau nach Ruddick et al. (2024), „Real-world validation of safe reinforcement learning, model
predictive control and decision tree-based home energy management systems“, Energy and AI,
Abschnitt 3.5.3:

  Zielfunktion (Gl. 12)   min  Σ_t (E_o,t·V_o,t − E_i,t·V_i,t)  +  V_p · max_t(P_o,t, P_p)
  Batteriemodell (Gl. 11) SOC_t+1 = SOC_t + (η·P_ch,t − P_di,t/η)·Δt
  Leistungsgrenzen (9b/9d) 0 ≤ P_ch, P_di ≤ P_max,  0 ≤ P_netz ≤ P_netz,max
  Bilanz (10a)            P_bezug − P_einspeisung = Last − PV + P_ch − P_di
  Binärvariable (10c)     kein gleichzeitiges Laden und Entladen
  Endbedingung            SOC am Horizontende ≥ Zielwert (weich: Unterschreitung kostet 100 Rp./kWh)

Unterschiede zum Paper: Der Bezugspreis V_o ist der dynamische EKZ-Tarif (inkl. Netz und
Abgaben, daher entfällt V_f), der Einspeisepreis V_i der Day-Ahead-Preis oder ein Fixtarif.
Zusätzlich gibt es optional Zyklenkosten (Verschleiss pro kWh Durchsatz), Abregelung der PV und
einen kleinen Tie-Breaker, der bei gleichen Kosten frühes Speichern bevorzugt (robust gegen
PV-Prognosefehler).
Ein EV-Ladepunkt ist nicht Teil dieses Rechners.

Gelöst wird mit HiGHS über scipy.optimize.milp.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix, csr_matrix

TERMINAL_PENALTY = 100.0  # Rp. pro kWh unter dem Ziel-SOC
# Tie-Breaker: Bei gleichen Kosten Energie lieber früher speichern (Rp. pro kWh im Speicher und Stunde).
# Ohne ihn ist es für die Optimierung oft egal, ob PV-Überschuss mittags oder erst abends geladen
# wird – fällt die PV dann schwächer aus als prognostiziert, fehlt die Energie.
HOLD_BONUS = 0.05


@dataclass
class Battery:
    capacity_kwh: float
    power_kw: float
    efficiency: float = 0.95
    soc_min: float = 0.1  # Anteil der Kapazität
    soc_max: float = 1.0
    cycle_cost: float = 0.0  # Rp./kWh Durchsatz (Laden + Entladen)


@dataclass
class Grid:
    import_kw: float = 11.0
    export_kw: float = 11.0
    allow_grid_charging: bool = True  # Batterie darf aus dem Netz laden
    allow_battery_export: bool = True  # Batterie darf ins Netz einspeisen
    peak_price: float = 0.0  # Rp. pro kW Bezugsspitze im Horizont (V_p im Paper)
    previous_peak_kw: float = 0.0  # P_p im Paper


@dataclass
class Plan:
    charge: np.ndarray  # kW
    discharge: np.ndarray  # kW
    grid_import: np.ndarray  # kW
    grid_export: np.ndarray  # kW
    pv_used: np.ndarray  # kW (nach Abregelung)
    soc: np.ndarray  # kWh, Länge N+1
    objective: float  # Rp.
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
    """Optimaler Fahrplan über den Horizont (alle Grössen je Zeitschritt, Preise in Rp./kWh).

    Ohne `terminal_value` gilt die (weiche) Endbedingung SOC_N ≥ soc_terminal wie im Paper.
    Mit `terminal_value = (Gutschrift, Kosten)` in Rp./kWh wird die Abweichung vom Ziel stattdessen
    bewertet: Mehr Energie am Ende wird gutgeschrieben, fehlende Energie kostet – so wie in der
    Abrechnung des Backtests.
    """
    n = len(pv)
    # Variablenblöcke: pc, pd, gi, ge, pvu, b (binär), peak (1), soc (n+1), Schlupf Endbedingung (1)
    PC, PD, GI, GE, PVU, B = (i * n for i in range(6))
    PEAK = 6 * n
    SOC = 6 * n + 1
    SLACK = SOC + n + 1
    UP, DOWN = SLACK + 1, SLACK + 2  # Abweichung vom Ziel-SOC (nur mit terminal_value)
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
        # Netzbezug nur für den Verbrauch – Laden also nur aus PV
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
        # Endbedingung: SOC_N + Schlupf ≥ Ziel
        add([(SOC + n, 1), (SLACK, 1)], target, np.inf)
    else:
        # SOC_N − Mehr + Weniger = Ziel
        add([(SOC + n, 1), (UP, -1), (DOWN, 1)], target, target)

    for t in range(n):
        # Bilanz (10a): gi − ge − pc + pd + pvu = Last
        add([(GI + t, 1), (GE + t, -1), (PC + t, -1), (PD + t, 1), (PVU + t, 1)], load[t], load[t])
        # Batteriemodell (11)
        add([(SOC + t + 1, 1), (SOC + t, -1), (PC + t, -eta * dt), (PD + t, dt / eta)], 0, 0)
        # Binär (10c): pc ≤ Pmax·b,  pd ≤ Pmax·(1 − b)
        add([(PC + t, 1), (B + t, -pmax)], -np.inf, 0)
        add([(PD + t, 1), (B + t, pmax)], -np.inf, pmax)
        # Spitzenlast: peak ≥ gi
        if grid.peak_price > 0:
            add([(PEAK, 1), (GI + t, -1)], 0, np.inf)
        if not grid.allow_battery_export:
            # Entladen höchstens so viel, wie im Haus verbraucht wird
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
        raise RuntimeError(f"MPC nicht lösbar: {res.message}")

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
