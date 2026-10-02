"""Statistisches Lastmodell für einen Haushalt.

Ohne Messdaten des Haushalts nutzen wir das Standardlastprofil-Prinzip (BDEW H0):
Tagesform je Tagtyp (Werktag / Samstag / Sonntag) × saisonaler Dynamisierungsfaktor,
skaliert auf den Jahresverbrauch. Die „tatsächliche“ Last im Backtest ist diese Prognose mit
einer reproduzierbaren, autokorrelierten Abweichung (AR(1)), damit die MPC – wie im
Paper – mit Prognosefehlern umgehen muss.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TZ = "Europe/Zurich"

# Stündliche Tagesformen (relative Leistung), angelehnt an H0
_WEEKDAY = [2.6, 2.2, 2.0, 1.9, 1.9, 2.2, 3.3, 4.5, 4.7, 4.5, 4.4, 4.6,
            5.0, 4.7, 4.2, 4.0, 4.2, 5.0, 6.2, 6.4, 6.0, 5.4, 4.5, 3.5]
_SATURDAY = [2.9, 2.4, 2.1, 2.0, 1.9, 2.0, 2.4, 3.3, 4.4, 5.0, 5.2, 5.4,
             5.6, 5.1, 4.5, 4.2, 4.3, 5.0, 5.9, 6.1, 5.8, 5.3, 4.7, 3.9]
_SUNDAY = [3.0, 2.5, 2.2, 2.0, 1.9, 1.9, 2.1, 2.7, 3.9, 5.0, 5.6, 6.2,
           6.4, 5.4, 4.6, 4.2, 4.3, 4.9, 5.8, 6.0, 5.7, 5.1, 4.4, 3.6]


def _dynamization(day_of_year: np.ndarray) -> np.ndarray:
    """BDEW-Dynamisierungsfunktion: Winter ~+24 %, Sommer ~-20 %."""
    d = day_of_year.astype(float)
    return -3.92e-10 * d**4 + 3.2e-7 * d**3 - 7.02e-5 * d**2 + 2.1e-3 * d + 1.24


def _shape(local: pd.DatetimeIndex) -> np.ndarray:
    profiles = np.array([_WEEKDAY, _SATURDAY, _SUNDAY])
    # Werktag 0, Samstag 1, Sonntag 2
    daytype = np.select([local.dayofweek == 5, local.dayofweek == 6], [1, 2], 0)
    return profiles[daytype, local.hour] * _dynamization(local.dayofyear.to_numpy())


# Normierung: mittlerer Formwert über ein ganzes Jahr (einmalig berechnet)
_YEAR = pd.date_range("2026-01-01", "2027-01-01", freq="15min", inclusive="left", tz=TZ)
_MEAN_SHAPE = float(_shape(_YEAR).mean())


def forecast(index: pd.DatetimeIndex, annual_kwh: float) -> pd.Series:
    """Erwartete Last in kW für jeden Zeitpunkt (Index: UTC, 15-Minuten-Raster)."""
    shape = _shape(index.tz_convert(TZ))
    mean_kw = annual_kwh / 8760
    return pd.Series(shape / _MEAN_SHAPE * mean_kw, index=index)


def actual(index: pd.DatetimeIndex, annual_kwh: float, seed: int = 7) -> pd.Series:
    """Synthetische „Ist-Last“: Prognose × autokorrelierte, log-normale Abweichung (σ ≈ 30 %)."""
    rng = np.random.default_rng(seed + int(index[0].timestamp()) // 86400)
    noise = np.empty(len(index))
    e = 0.0
    for i in range(len(index)):
        e = 0.85 * e + rng.normal(0, 0.16)
        noise[i] = e
    return forecast(index, annual_kwh) * np.exp(noise - 0.5 * 0.3**2)
