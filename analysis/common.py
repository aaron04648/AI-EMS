"""Shared paths and constants for the analysis pipeline (steps 01–04)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
OUT = ROOT / "output"

TZ = "Europe/Zurich"
START = pd.Timestamp("2024-01-01", tz="UTC")

# Bidding zones (energy-charts /price): Switzerland + neighbours that drive the CH price.
BIDDING_ZONES = {"ch": "CH", "de": "DE-LU", "fr": "FR", "it": "IT-North", "at": "AT"}

# Countries for actual generation (/public_power) and day-ahead forecasts (/public_power_forecast).
POWER_COUNTRIES = ["ch", "de", "fr", "it", "at"]
# Offshore wind only for countries with a coastline and a published forecast.
FORECASTS = {"ch": ["solar", "wind_onshore", "load"], "de": ["solar", "wind_onshore", "wind_offshore", "load"],
             "fr": ["solar", "wind_onshore", "wind_offshore", "load"], "it": ["solar", "wind_onshore", "load"],
             "at": ["solar", "wind_onshore", "load"]}

# Weather points for the Open-Meteo forecasts (previous-day run, i.e. available on the previous day).
WEATHER_POINTS = {
    "zurich": (47.377, 8.540),
    "bern": (46.948, 7.447),
    "geneva": (46.204, 6.143),
    "lugano": (46.004, 8.951),
    "sion": (46.233, 7.360),
    "munich": (48.137, 11.575),
    "hamburg": (53.551, 9.994),
}
WEATHER_VARS = ["temperature_2m", "shortwave_radiation", "cloud_cover", "wind_speed_100m", "precipitation"]


def processed_hourly() -> pd.DataFrame:
    """Hourly combined dataset from step 01 (index: UTC)."""
    df = pd.read_csv(PROCESSED / "hourly.csv", index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def out_dir(name: str) -> Path:
    path = OUT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


# Uniform chart style (reference palette, fixed order, thin lines, subtle grid).
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def plot_style() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from cycler import cycler

    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelsize": 10, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False, "axes.prop_cycle": cycler(color=COLORS),
        "xtick.color": INK_2, "ytick.color": INK_2, "xtick.labelsize": 9, "ytick.labelsize": 9,
        "lines.linewidth": 1.5, "legend.frameon": False, "legend.fontsize": 9,
        "figure.dpi": 110, "savefig.dpi": 150, "savefig.bbox": "tight", "font.family": "sans-serif",
    })


# Evaluation: last ~12 months as holdout (from the switch to 15 min day-ahead in Oct 2025).
TEST_START = pd.Timestamp("2025-10-01", tz=TZ).tz_convert("UTC")


def profile_forecast(y: pd.Series, days: int = 14) -> pd.Series:
    """For each day D: mean of the last `days` days up to D-1, per hour and day type (weekday/weekend)."""
    local = y.index.tz_convert(TZ)
    frame = pd.DataFrame({"y": y.values, "date": local.normalize(), "hour": local.hour,
                          "weekend": local.day_of_week >= 5}, index=y.index)
    table = frame.pivot_table(index="date", columns=["weekend", "hour"], values="y")
    # Per day type: days of the other type are NaN and drop out of the mean; shift(1) → past only
    roll = table.rolling(days, min_periods=2).mean().shift(1).stack(level=[0, 1], future_stack=True)
    key = pd.MultiIndex.from_arrays([frame["date"], frame["weekend"], frame["hour"]])
    return pd.Series(roll.reindex(key).to_numpy(), index=y.index)


def metrics(y_true: pd.Series, y_pred: pd.Series) -> dict[str, float]:
    """MAE, normalised MAE (MAE / mean absolute price) and RMSE.

    nMAE is the target metric "MAE below 10 %": a classic MAPE is undefined/explodes for prices near 0 or
    negative prices, so it is normalised by the mean absolute price.
    """
    pair = pd.concat([y_true, y_pred], axis=1).dropna()
    err = pair.iloc[:, 0] - pair.iloc[:, 1]
    mae = err.abs().mean()
    return {"MAE": mae, "nMAE_%": 100 * mae / pair.iloc[:, 0].abs().mean(),
            "RMSE": float(np.sqrt((err ** 2).mean())), "n": len(pair)}
