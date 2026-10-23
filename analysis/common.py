"""Gemeinsame Pfade und Konstanten für die Analyse-Pipeline (Schritte 01–04)."""

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

# Gebotszonen (energy-charts /price): Schweiz + Nachbarn, die den CH-Preis treiben.
BIDDING_ZONES = {"ch": "CH", "de": "DE-LU", "fr": "FR", "it": "IT-North", "at": "AT"}

# Länder für Ist-Erzeugung (/public_power) und Day-Ahead-Prognosen (/public_power_forecast).
POWER_COUNTRIES = ["ch", "de", "fr", "it", "at"]
# Offshore-Wind nur für Länder mit Meeresküste und publizierter Prognose.
FORECASTS = {"ch": ["solar", "wind_onshore", "load"], "de": ["solar", "wind_onshore", "wind_offshore", "load"],
             "fr": ["solar", "wind_onshore", "wind_offshore", "load"], "it": ["solar", "wind_onshore", "load"],
             "at": ["solar", "wind_onshore", "load"]}

# Wetterpunkte für die Open-Meteo-Prognosen (Vortageslauf, d. h. am Vortag verfügbar).
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
    """Stündlicher Gesamtdatensatz aus Schritt 01 (Index: UTC)."""
    df = pd.read_csv(PROCESSED / "hourly.csv", index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def out_dir(name: str) -> Path:
    path = OUT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


# Einheitlicher Grafikstil (Referenzpalette, feste Reihenfolge, dünne Linien, dezentes Raster).
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


# Evaluation: letzte ~12 Monate als Holdout (ab Umstellung auf 15-min-Day-Ahead im Okt. 2025).
TEST_START = pd.Timestamp("2025-10-01", tz=TZ).tz_convert("UTC")


def metrics(y_true: pd.Series, y_pred: pd.Series) -> dict[str, float]:
    """MAE, normierter MAE (MAE / mittlerer Absolutpreis) und RMSE.

    nMAE ist die Zielgrösse "MAE unter 10 %": Ein klassischer MAPE ist bei Preisen nahe 0 bzw.
    negativen Preisen nicht definiert/explodiert, deshalb wird auf den mittleren Absolutpreis normiert.
    """
    pair = pd.concat([y_true, y_pred], axis=1).dropna()
    err = pair.iloc[:, 0] - pair.iloc[:, 1]
    mae = err.abs().mean()
    return {"MAE": mae, "nMAE_%": 100 * mae / pair.iloc[:, 0].abs().mean(),
            "RMSE": float(np.sqrt((err ** 2).mean())), "n": len(pair)}
