"""Closed-loop simulation and schedule.

Backtest over the last days – as in the paper, the strategies are compared under equal conditions,
except that the houses here are simulated instead of rebuilt in the lab:

  no battery      PV covers consumption, surplus is fed in
  RBC             self-consumption rule: charge surplus, discharge deficit (paper 3.5.2)
  MPC             re-optimised every 15 min with forecasts (paper 3.5.3)
  Optimum         same optimisation with perfect foresight (theoretical lower bound)

The MPC only knows what it can know at the respective point in time:
  - PV: MeteoSwiss forecast from the previous day (ICON-CH2) → statistical PV model
  - Load: statistical load profile
  - Prices: published EKZ tariffs (by 18:00 for the following day), afterwards statistical forecast
Billing uses the actual values: PV from MeteoSwiss measurements, synthetic actual load.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..models import load_model, pv_model, tariff_forecast
from ..schemas import SimulationRequest
from ..sources import meteo, pvgis, tariffs
from .mpc import Battery, Grid, solve

TZ = "Europe/Zurich"
DT = 0.25
STEP = pd.Timedelta(minutes=15)
HORIZON = 96  # 24 h


def _quarters(hourly: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Distribute hourly means (timestamp = end of hour) to quarter hours (timestamp = start)."""
    hour_end = index.floor("h") + pd.Timedelta(hours=1)
    return pd.Series(hourly.reindex(hour_end).to_numpy(), index=index).ffill().fillna(0.0)


@dataclass
class Inputs:
    index: pd.DatetimeIndex
    pv_actual: np.ndarray
    pv_forecast: np.ndarray
    load_actual: np.ndarray
    load_forecast: np.ndarray
    price_import: pd.Series  # actual tariff (ct/kWh), beyond the window as far as published
    price_export: pd.Series
    annual_kwh: float


class PriceOracle:
    """Returns for a point in time the prices that were known then (+ statistical forecast)."""

    def __init__(self, actual: pd.Series):
        self.actual = actual.sort_index()
        self._cache: dict[pd.Timestamp, pd.Series] = {}

    def view(self, now: pd.Timestamp, index: pd.DatetimeIndex) -> np.ndarray:
        published = tariffs.published_until(now)
        if published not in self._cache:
            known = self.actual[self.actual.index < published]
            history = known[known.index >= published - pd.Timedelta(days=21)]
            horizon = pd.date_range(now.floor("D") - pd.Timedelta(days=1), published + pd.Timedelta(days=2),
                                    freq="15min", inclusive="left")
            self._cache[published] = tariff_forecast.complete(known, horizon, history)
        return self._cache[published].reindex(index).ffill().bfill().to_numpy()


# ---------------------------------------------------------------------------
# Applying a setpoint to the "real" system (simplified safety layer)
# ---------------------------------------------------------------------------

def _apply(setpoint: float, pv: float, load: float, soc: float, bat: Battery, grid: Grid,
           price_export: float, curtail: bool) -> dict:
    cap = bat.capacity_kwh
    charge = discharge = 0.0
    if setpoint > 0:
        room = (bat.soc_max * cap - soc) / (bat.efficiency * DT)
        charge = max(0.0, min(setpoint, bat.power_kw, room))
        if not grid.allow_grid_charging:
            charge = min(charge, max(0.0, pv - load))
    elif setpoint < 0:
        avail = (soc - bat.soc_min * cap) * bat.efficiency / DT
        discharge = max(0.0, min(-setpoint, bat.power_kw, avail))
        if not grid.allow_battery_export:
            discharge = min(discharge, max(0.0, load))
    soc += (charge * bat.efficiency - discharge / bat.efficiency) * DT

    net = load - pv + charge - discharge
    grid_import = max(net, 0.0)
    surplus = max(-net, 0.0)
    # Curtailment at negative feed-in price (smart strategies only) and at the export limit
    grid_export = 0.0 if (curtail and price_export < 0) else min(surplus, grid.export_kw)
    return {"charge": charge, "discharge": discharge, "import": grid_import, "export": grid_export,
            "curtailed": surplus - grid_export, "soc": soc}


def _rbc(pv: float, load: float, soc: float, bat: Battery) -> float:
    """Self-consumption rule: setpoint = PV surplus (positive) or deficit (negative)."""
    return pv - load


def _run(inp: Inputs, bat: Battery, grid: Grid, soc0: float, strategy: str, oracle_import: PriceOracle,
         oracle_export: PriceOracle | None, export_fixed: float | None) -> dict:
    n = len(inp.index)
    keys = ["charge", "discharge", "import", "export", "curtailed", "soc"]
    out = {k: np.zeros(n) for k in keys}
    soc = soc0
    vo = inp.price_import.reindex(inp.index).to_numpy()
    vi = inp.price_export.reindex(inp.index).to_numpy()

    # Valuation of the final state of charge (ct/kWh): surplus saves later import, a shortfall must
    # be bought back with charging losses. The optimum uses the same values in its objective.
    credit, debit = np.mean(vo) * bat.efficiency, np.mean(vo) / bat.efficiency

    optimal = None
    if strategy == "optimal":
        optimal = solve(inp.pv_actual, inp.load_actual, vo, vi, soc0, soc0, bat, grid,
                        terminal_value=(credit, debit), hold_bonus=0.0)

    for t in range(n):
        now = inp.index[t]
        if strategy == "none":
            setpoint = 0.0
        elif strategy == "rbc":
            setpoint = _rbc(inp.pv_actual[t], inp.load_actual[t], soc, bat)
        elif strategy == "optimal":
            setpoint = optimal.charge[t] - optimal.discharge[t]
        else:  # MPC with rolling horizon
            idx = pd.date_range(now, periods=HORIZON, freq="15min")
            pv_fc = inp.pv_forecast[t:t + HORIZON].copy()  # extends one day beyond the window
            load_fc = load_model.forecast(idx, inp.annual_kwh).to_numpy()
            # For the current time step the measured value is known (as in the paper: state + forecast)
            pv_fc[0] = inp.pv_actual[t]
            load_fc[0] = inp.load_actual[t]
            price_i = oracle_import.view(now, idx)
            price_e = oracle_export.view(now, idx) if oracle_export else np.full(HORIZON, export_fixed)
            plan = solve(pv_fc, load_fc, price_i, price_e, soc, soc0, bat, grid)
            setpoint = plan.charge[0] - plan.discharge[0]

        step = _apply(setpoint, inp.pv_actual[t], inp.load_actual[t], soc, bat, grid, vi[t],
                      curtail=strategy in ("mpc", "optimal"))
        soc = step["soc"]
        for k in keys:
            out[k][t] = step[k]

    energy_cost = float(np.sum(out["import"] * vo - out["export"] * vi) * DT / 100)
    # Value the final state of charge so that strategies do not win by draining the battery
    delta = soc - soc0
    soc_value = float(delta * (credit if delta > 0 else debit) / 100)
    pv_total = float(np.sum(inp.pv_actual) * DT)
    load_total = float(np.sum(inp.load_actual) * DT)
    imp = float(np.sum(out["import"]) * DT)
    exp = float(np.sum(out["export"]) * DT)
    return {
        "series": {k: np.round(out[k], 3).tolist() for k in keys},
        "cost_chf": round(energy_cost, 2),
        "cost_adjusted_chf": round(energy_cost - soc_value, 2),
        "import_kwh": round(imp, 1),
        "export_kwh": round(exp, 1),
        "self_consumption": round((pv_total - exp - float(np.sum(out["curtailed"]) * DT)) / pv_total, 3) if pv_total else None,
        "autarky": round(1 - imp / load_total, 3) if load_total else None,
        "cycles": round(float(np.sum(out["discharge"]) * DT) / bat.capacity_kwh, 2) if bat.capacity_kwh else 0,
    }


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def run(req: SimulationRequest) -> dict:
    loc, pv_cfg, bat_cfg = req.location, req.pv, req.battery
    model = pv_model.get_model(loc.lat, loc.lon, pv_cfg.tilt, pv_cfg.azimuth, pv_cfg.loss)
    yearly = pvgis.yearly_yield(loc.lat, loc.lon, pv_cfg.tilt, pv_cfg.azimuth, pv_cfg.loss, pv_cfg.kwp)

    bat = Battery(
        capacity_kwh=bat_cfg.capacity_kwh,
        power_kw=bat_cfg.power_kw,
        efficiency=bat_cfg.efficiency,
        soc_min=bat_cfg.soc_min,
        soc_max=bat_cfg.soc_max,
        cycle_cost=bat_cfg.cycle_cost,
    )
    grid = Grid(
        import_kw=req.grid.limit_kw,
        export_kw=req.grid.limit_kw,
        allow_grid_charging=req.grid.allow_grid_charging,
        allow_battery_export=req.grid.allow_battery_export,
    )
    soc0 = bat_cfg.soc_start * bat.capacity_kwh

    # ---------- Time window ----------
    now = pd.Timestamp.now(tz="UTC").floor("15min")
    end = now.tz_convert(TZ).normalize().tz_convert("UTC")
    start = end - pd.Timedelta(days=req.days)
    index = pd.date_range(start, end, freq="15min", inclusive="left")
    extended = pd.date_range(start, end + pd.Timedelta(days=1), freq="15min", inclusive="left")

    # ---------- Weather & PV ----------
    weather, station = meteo.backtest(loc.lat, loc.lon, req.days)
    pv_actual_h = model.predict(weather[["ghi", "temp"]], loc.lat, loc.lon, pv_cfg.kwp)
    pv_fc_h = model.predict(weather[["ghi_fc", "temp_fc"]].set_axis(["ghi", "temp"], axis=1), loc.lat, loc.lon, pv_cfg.kwp)
    pv_actual = _quarters(pv_actual_h, index).to_numpy()
    pv_forecast = _quarters(pv_fc_h, extended).to_numpy()

    # ---------- Tariffs ----------
    fetch_from = (start - pd.Timedelta(days=22)).to_pydatetime()
    fetch_to = (end + pd.Timedelta(days=2)).to_pydatetime()
    price_import = tariffs.ekz_tariff("integrated_400D", fetch_from, fetch_to)
    if price_import.empty:
        raise RuntimeError("EKZ tariff integrated_400D not available")
    oracle_import = PriceOracle(price_import)
    if req.tariff.feed_in == "spot":
        price_export = tariffs.spot_ch(fetch_from, fetch_to)
        oracle_export: PriceOracle | None = PriceOracle(price_export)
        export_fixed = None
    else:
        price_export = pd.Series(req.tariff.feed_in_fixed, index=price_import.index)
        oracle_export = None
        export_fixed = req.tariff.feed_in_fixed

    # ---------- Load ----------
    load_actual = load_model.actual(index, req.household.annual_kwh).to_numpy()
    load_forecast = load_model.forecast(index, req.household.annual_kwh).to_numpy()

    # Actual prices in the window; fill missing values (e.g. energy-charts unreachable) statistically
    window_import = tariff_forecast.complete(price_import, index, price_import)
    window_export = tariff_forecast.complete(price_export, index, price_export)
    estimated = int(price_import.reindex(index).isna().sum() + price_export.reindex(index).isna().sum())
    inp = Inputs(index, pv_actual, pv_forecast, load_actual, load_forecast,
                 window_import, window_export, req.household.annual_kwh)

    results = {}
    for strategy in ("none", "rbc", "mpc", "optimal"):
        results[strategy] = _run(inp, bat, grid, soc0, strategy, oracle_import, oracle_export, export_fixed)

    no_pv_cost = float(np.sum(load_actual * inp.price_import.to_numpy()) * DT / 100)
    pv_error = pv_forecast[: len(index)] - pv_actual

    backtest = {
        "start": int(start.timestamp()),
        "step": 900,
        "days": req.days,
        "station": station,
        "pv_actual": np.round(pv_actual, 3).tolist(),
        "pv_forecast": np.round(pv_forecast[: len(index)], 3).tolist(),
        "load_actual": np.round(load_actual, 3).tolist(),
        "load_forecast": np.round(load_forecast, 3).tolist(),
        "price_import": np.round(inp.price_import.to_numpy(), 2).tolist(),
        "price_export": np.round(inp.price_export.to_numpy(), 2).tolist(),
        "no_pv_cost_chf": round(no_pv_cost, 2),
        "pv_forecast_mae_kw": round(float(np.mean(np.abs(pv_error))), 3),
        "estimated_prices": estimated,
        "strategies": results,
    }

    return {
        "pv_model": {
            "r2": round(model.r2, 4),
            "mae_w_per_kwp": round(model.mae_w_per_kwp, 1),
            "train_rows": model.train_rows,
            "test_year": model.test_year,
            "annual_kwh": round(yearly["annual_kwh"]),
            "monthly_kwh": [round(v, 1) for v in yearly["monthly_kwh"]],
        },
        "backtest": backtest,
        "tariffs": _tariff_days(price_import, price_export, now),
        "plan": _plan(req, model, bat, grid, soc0, now, price_import, price_export, oracle_export is None),
    }


def _tariff_days(price_import: pd.Series, price_export: pd.Series, now: pd.Timestamp) -> dict:
    """Published tariffs of the last 14 days up to the end of publication – for the exchange view."""
    start = (now.tz_convert(TZ).normalize() - pd.Timedelta(days=13)).tz_convert("UTC")
    end = tariffs.published_until(now)
    index = pd.date_range(start, end, freq="15min", inclusive="left")
    vo = price_import.reindex(index)
    vi = price_export.reindex(index)
    return {
        "start": int(start.timestamp()),
        "step": 900,
        "import": [None if pd.isna(v) else round(float(v), 3) for v in vo],
        "export": [None if pd.isna(v) else round(float(v), 2) for v in vi],
    }


def _plan(req: SimulationRequest, model: pv_model.PvModel, bat: Battery, grid: Grid, soc0: float,
          now: pd.Timestamp, price_import: pd.Series, price_export: pd.Series, export_is_fixed: bool) -> dict:
    """Schedule from now: until the end of the published tariffs, at least 24 h."""
    published = tariffs.published_until(now)
    horizon_end = max(published, now + pd.Timedelta(hours=24))
    index = pd.date_range(now, horizon_end, freq="15min", inclusive="left")

    weather = meteo.forecast(req.location.lat, req.location.lon, days=2)
    pv = _quarters(model.predict(weather, req.location.lat, req.location.lon, req.pv.kwp), index).to_numpy()
    load = load_model.forecast(index, req.household.annual_kwh).to_numpy()

    vo = PriceOracle(price_import).view(now, index)
    vi = np.full(len(index), req.tariff.feed_in_fixed) if export_is_fixed else PriceOracle(price_export).view(now, index)
    known = (index < published).tolist()

    plan = solve(pv, load, vo, vi, soc0, soc0, bat, grid)
    return {
        "start": int(now.timestamp()),
        "step": 900,
        "published_until": int(published.timestamp()),
        "known": known,
        "pv": np.round(pv, 3).tolist(),
        "load": np.round(load, 3).tolist(),
        "price_import": np.round(vo, 2).tolist(),
        "price_export": np.round(vi, 2).tolist(),
        "charge": np.round(plan.charge, 3).tolist(),
        "discharge": np.round(plan.discharge, 3).tolist(),
        "grid_import": np.round(plan.grid_import, 3).tolist(),
        "grid_export": np.round(plan.grid_export, 3).tolist(),
        "soc": np.round(plan.soc[:-1] / bat.capacity_kwh * 100, 1).tolist() if bat.capacity_kwh else [],
        "cost_chf": round(plan.objective / 100, 2),
    }
