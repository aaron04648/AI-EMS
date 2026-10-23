"""Feature-Matrix für die 24h-Day-Ahead-Prognose des CH-Preises.

Prognosesituation (realistisch, ohne Leakage):
  Am Vortag D-1 um ca. 11:00 (vor Gebotsschluss der CH-Auktion) werden die 24 Stundenpreise
  des Liefertags D prognostiziert. Bekannt sind dann:
    * alle Preise bis Ende D-1 (die Auktion für D-1 lief bereits an D-2)  → Lags ≥ 24 h
    * Ist-Erzeugung/Last nur bis ca. D-1 vormittags                       → nur Lags ≥ 48 h
    * Day-Ahead-Prognosen der Netzbetreiber für D (Wind, Solar, Last)     → für Stunde t direkt
    * Wetterprognosen des Vortageslaufs (Open-Meteo previous_day1)          → für Stunde t direkt
"""

from __future__ import annotations

import warnings

import holidays
import numpy as np
import pandas as pd

from common import TZ

TARGET = "price_ch"
NEIGHBOURS = ["de", "fr", "it", "at"]

# Gruppen für die Erklärung (SHAP) – Präfix → verständlicher Treiber
DRIVER_GROUPS = {
    "Preisniveau (Lags CH)": ("lag_ch_", "roll_ch_"),
    "Nachbarpreise (Lags)": ("lag_de", "lag_fr", "lag_it", "lag_at", "roll_de", "roll_it", "roll_fr", "roll_at"),
    "Solar-Prognose": ("fc_de_solar", "fc_ch_solar", "fc_fr_solar", "fc_it_solar", "fc_at_solar", "wx_rad"),
    "Wind-Prognose": ("fc_de_wind", "fc_fr_wind", "fc_it_wind", "fc_at_wind", "fc_ch_wind", "wx_wind"),
    "Last/Residuallast-Prognose": ("fc_de_load", "fc_ch_load", "fc_fr_load", "fc_it_load", "fc_at_load",
                                   "fc_resid", "wx_temp"),
    "Wasserkraft & Austausch (Lags)": ("act_",),
    "Kalender": ("hour", "dow", "weekend", "holiday", "month", "doy"),
    "Wetter sonstiges": ("wx_",),
}


def driver_group(feature: str) -> str:
    for group, prefixes in DRIVER_GROUPS.items():
        if feature.startswith(prefixes):
            return group
    return "Sonstiges"


def _cyc(values: np.ndarray, period: int) -> tuple[np.ndarray, np.ndarray]:
    angle = 2 * np.pi * values / period
    return np.sin(angle), np.cos(angle)


def build(df: pd.DataFrame, use_tso_forecasts: bool = True) -> pd.DataFrame:
    """Feature-Matrix (eine Zeile pro Stunde, Index UTC) inkl. Zielspalte `y`."""
    with warnings.catch_warnings():  # spaltenweiser Aufbau ist hier lesbarer als ein grosses concat
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        return _build(df, use_tso_forecasts).copy()


def _build(df: pd.DataFrame, use_tso_forecasts: bool) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    X["y"] = df[TARGET]

    # --- Kalender (Lokalzeit) -------------------------------------------------------------
    local = df.index.tz_convert(TZ)
    ch_holidays = holidays.country_holidays("CH", subdiv="ZH", years=range(local.year.min(), local.year.max() + 1))
    X["hour"] = local.hour
    X["dow"] = local.day_of_week
    X["weekend"] = (local.day_of_week >= 5).astype(int)
    X["holiday"] = pd.Index(local.date).map(lambda d: d in ch_holidays).astype(int)
    X["month_sin"], X["month_cos"] = _cyc(local.month.to_numpy() - 1, 12)
    X["hour_sin"], X["hour_cos"] = _cyc(local.hour.to_numpy(), 24)
    X["doy_sin"], X["doy_cos"] = _cyc(local.day_of_year.to_numpy() - 1, 365)

    # --- Preis-Lags (CH und Nachbarn) ----------------------------------------------------------
    p = df[TARGET]
    for lag in (24, 48, 72, 168):
        X[f"lag_ch_{lag}"] = p.shift(lag)
    # Kennzahlen des Vortags D-1 (zum Prognosezeitpunkt vollständig bekannt) – gleicher Wert für alle 24 h von D
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

    # --- Day-Ahead-Prognosen der Netzbetreiber für Stunde t ------------------------------------------
    if use_tso_forecasts:
        fc = [c for c in df if c.startswith("fc_")]
        for c in fc:
            X[c] = df[c]
        for country in ("de", "ch", "fr", "it", "at"):
            load = f"fc_{country}_load"
            vre = [c for c in (f"fc_{country}_solar", f"fc_{country}_wind_onshore", f"fc_{country}_wind_offshore") if c in df]
            if load in df and vre:
                # fehlt eine Komponente (noch nicht publiziert), bleibt die Residuallast leer statt verfälscht
                X[f"fc_resid_{country}"] = df[load] - df[vre].sum(axis=1, skipna=False)
        resid = [c for c in X if c.startswith("fc_resid_")]
        if resid:
            X["fc_resid_sum"] = X[resid].sum(axis=1, skipna=False)
            # Abweichung vom Tagesmittel: erklärt die Form der Tageskurve
            X["fc_resid_sum_rel"] = X["fc_resid_sum"] - X["fc_resid_sum"].groupby(day.values).transform("mean")
            X["fc_resid_sum_dmean"] = X["fc_resid_sum"].groupby(day.values).transform("mean")
        # Änderung ggü. Vortag: Preisniveau von gestern ist bekannt, erklärt werden muss die Differenz
        for c in [c for c in X if c.startswith("fc_") and not c.endswith(("_rel", "_dmean"))]:
            X[f"{c}_d24"] = X[c] - X[c].shift(24)
        if "fc_resid_sum_dmean" in X:
            X["fc_resid_sum_dmean_d24"] = X["fc_resid_sum_dmean"] - X["fc_resid_sum_dmean"].shift(24)

    # --- Wetterprognosen (Vortageslauf) ---------------------------------------------------------------
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

    # --- Ist-Werte nur mit Lag ≥ 48 h (Wasserkraft, Austausch) ----------------------------------------
    for c in ("act_ch_hydro_water_reservoir", "act_ch_hydro_pumped_storage", "act_ch_hydro_run_of_river",
              "act_ch_nuclear", "cbet_ch_sum", "cbet_ch_italy", "cbet_ch_germany", "cbet_ch_france"):
        if c in df:
            name = c.replace("cbet_", "act_cbet_")
            X[f"{name}_lag48"] = df[c].shift(48)
            X[f"{name}_7d_lag48"] = df[c].shift(48).rolling(168, min_periods=24).mean()

    return X


def feature_columns(X: pd.DataFrame) -> list[str]:
    return [c for c in X.columns if c != "y"]
