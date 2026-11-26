"""Feature matrix for the 24h day-ahead forecast of the CH price.

Forecast situation (realistic, no leakage):
  On the previous day D-1 at approx. 11:00 (before gate closure of the CH auction) the 24 hourly prices
  of delivery day D are forecast. Known at that point:
    * all prices up to the end of D-1 (the auction for D-1 already ran on D-2)  → lags ≥ 24 h
    * actual generation/load only up to approx. D-1 morning                     → lags ≥ 48 h only
    * day-ahead forecasts of the grid operators for D (wind, solar, load)       → directly for hour t
    * weather forecasts of the previous-day run (Open-Meteo previous_day1)      → directly for hour t
"""

from __future__ import annotations

import warnings

import holidays
import numpy as np
import pandas as pd

from common import TZ

TARGET = "price_ch"
NEIGHBOURS = ["de", "fr", "it", "at"]

# Groups for the explanation (SHAP) – prefix → understandable driver
DRIVER_GROUPS = {
    "Price level (lags CH)": ("lag_ch_", "roll_ch_"),
    "Neighbouring prices (lags)": ("lag_de", "lag_fr", "lag_it", "lag_at", "roll_de", "roll_it", "roll_fr", "roll_at"),
    "Solar forecast": ("fc_de_solar", "fc_ch_solar", "fc_fr_solar", "fc_it_solar", "fc_at_solar", "wx_rad"),
    "Wind forecast": ("fc_de_wind", "fc_fr_wind", "fc_it_wind", "fc_at_wind", "fc_ch_wind", "wx_wind"),
    "Load/residual load forecast": ("fc_de_load", "fc_ch_load", "fc_fr_load", "fc_it_load", "fc_at_load",
                                    "fc_resid", "wx_temp"),
    "Hydro & exchange (lags)": ("act_",),
    "Calendar": ("hour", "dow", "weekend", "holiday", "month", "doy"),
    "Other weather": ("wx_",),
}


def driver_group(feature: str) -> str:
    for group, prefixes in DRIVER_GROUPS.items():
        if feature.startswith(prefixes):
            return group
    return "Other"


def _cyc(values: np.ndarray, period: int) -> tuple[np.ndarray, np.ndarray]:
    angle = 2 * np.pi * values / period
    return np.sin(angle), np.cos(angle)


def build(df: pd.DataFrame, use_tso_forecasts: bool = True) -> pd.DataFrame:
    """Feature matrix (one row per hour, index UTC) incl. target column `y`."""
    with warnings.catch_warnings():  # building column by column is more readable here than one big concat
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        return _build(df, use_tso_forecasts).copy()


def _build(df: pd.DataFrame, use_tso_forecasts: bool) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    X["y"] = df[TARGET]

    # --- Calendar (local time) ------------------------------------------------------------
    local = df.index.tz_convert(TZ)
    ch_holidays = holidays.country_holidays("CH", subdiv="ZH", years=range(local.year.min(), local.year.max() + 1))
    X["hour"] = local.hour
    X["dow"] = local.day_of_week
    X["weekend"] = (local.day_of_week >= 5).astype(int)
    X["holiday"] = pd.Index(local.date).map(lambda d: d in ch_holidays).astype(int)
    X["month_sin"], X["month_cos"] = _cyc(local.month.to_numpy() - 1, 12)
    X["hour_sin"], X["hour_cos"] = _cyc(local.hour.to_numpy(), 24)
    X["doy_sin"], X["doy_cos"] = _cyc(local.day_of_year.to_numpy() - 1, 365)

    # --- Price lags (CH and neighbours) --------------------------------------------------------
    p = df[TARGET]
    for lag in (24, 48, 72, 168):
        X[f"lag_ch_{lag}"] = p.shift(lag)
    # Statistics of the previous day D-1 (fully known at forecast time) – same value for all 24 h of D
    day = pd.Series(local.normalize(), index=df.index)
    daily = p.groupby(day.values).agg(["mean", "min", "max", "std"])
    prev = daily.shift(1).reindex(day.values)
    for stat in ("mean", "min", "max", "std"):
        X[f"roll_ch_d1_{stat}"] = prev[stat].to_numpy()
    X["roll_ch_7d_mean"] = daily["mean"].rolling(7).mean().shift(1).reindex(day.values).to_numpy()
    for n in NEIGHBOURS:
        if f"price_{n}" in df:
            X[f"lag_{n}_24"] = df[f"price_{n}"].shift(24)
            X[f"lag_{n}_168"] = df[f"price_{n}"].shift(168)
            X[f"roll_{n}_d1_mean"] = df[f"price_{n}"].groupby(day.values).mean().shift(1).reindex(day.values).to_numpy()

    # --- Day-ahead forecasts of the grid operators for hour t ---------------------------------------
    if use_tso_forecasts:
        fc = [c for c in df if c.startswith("fc_")]
        for c in fc:
            X[c] = df[c]
        for country in ("de", "ch", "fr", "it", "at"):
            load = f"fc_{country}_load"
            vre = [c for c in (f"fc_{country}_solar", f"fc_{country}_wind_onshore", f"fc_{country}_wind_offshore") if c in df]
            if load in df and vre:
                # if a component is missing (not yet published), the residual load stays empty instead of distorted
                X[f"fc_resid_{country}"] = df[load] - df[vre].sum(axis=1, skipna=False)
        resid = [c for c in X if c.startswith("fc_resid_")]
        if resid:
            X["fc_resid_sum"] = X[resid].sum(axis=1, skipna=False)
            # Deviation from the daily mean: explains the shape of the daily curve
            X["fc_resid_sum_rel"] = X["fc_resid_sum"] - X["fc_resid_sum"].groupby(day.values).transform("mean")
            X["fc_resid_sum_dmean"] = X["fc_resid_sum"].groupby(day.values).transform("mean")
        # Change vs. previous day: yesterday's price level is known, the difference is what must be explained
        for c in [c for c in X if c.startswith("fc_") and not c.endswith(("_rel", "_dmean"))]:
            X[f"{c}_d24"] = X[c] - X[c].shift(24)
        if "fc_resid_sum_dmean" in X:
            X["fc_resid_sum_dmean_d24"] = X["fc_resid_sum_dmean"] - X["fc_resid_sum_dmean"].shift(24)

    # --- Weather forecasts (previous-day run) ---------------------------------------------------------
    wx = {"temperature_2m": "wx_temp", "shortwave_radiation": "wx_rad", "wind_speed_100m": "wx_wind",
          "cloud_cover": "wx_cloud", "precipitation": "wx_precip"}
    for var, short in wx.items():
        ch_cols = [c for c in df if c.startswith("wx_") and c.endswith(var)
                   and not c.startswith(("wx_munich", "wx_hamburg"))]
        if ch_cols:
            X[f"{short}_ch"] = df[ch_cols].mean(axis=1)
        for city in ("munich", "hamburg"):
            if f"wx_{city}_{var}" in df and var in ("shortwave_radiation", "wind_speed_100m"):
                X[f"{short}_{city}"] = df[f"wx_{city}_{var}"]
    if "wx_precip_ch" in X:
        X["wx_precip_ch_7d"] = X["wx_precip_ch"].rolling(168, min_periods=24).sum()

    # --- Actual values only with lag ≥ 48 h (hydro, exchange) -----------------------------------------
    for c in ("act_ch_hydro_water_reservoir", "act_ch_hydro_pumped_storage", "act_ch_hydro_run_of_river",
              "act_ch_nuclear", "cbet_ch_sum", "cbet_ch_italy", "cbet_ch_germany", "cbet_ch_france"):
        if c in df:
            name = c.replace("cbet_", "act_cbet_")
            X[f"{name}_lag48"] = df[c].shift(48)
            X[f"{name}_7d_lag48"] = df[c].shift(48).rolling(168, min_periods=24).mean()

    return X


def feature_columns(X: pd.DataFrame) -> list[str]:
    return [c for c in X.columns if c != "y"]
