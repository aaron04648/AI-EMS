"""Statistical load model for a household.

Without measured data from the household we use the standard load profile principle (BDEW H0):
daily shape per day type (weekday / Saturday / Sunday) × seasonal dynamisation factor,
scaled to the annual consumption. The "actual" load in the backtest is this forecast with
a reproducible, autocorrelated deviation (AR(1)), so that the MPC – as in the
paper – has to cope with forecast errors.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TZ = "Europe/Zurich"

# Hourly daily shapes (relative power), based on H0
_WEEKDAY = [2.6, 2.2, 2.0, 1.9, 1.9, 2.2, 3.3, 4.5, 4.7, 4.5, 4.4, 4.6,
            5.0, 4.7, 4.2, 4.0, 4.2, 5.0, 6.2, 6.4, 6.0, 5.4, 4.5, 3.5]
_SATURDAY = [2.9, 2.4, 2.1, 2.0, 1.9, 2.0, 2.4, 3.3, 4.4, 5.0, 5.2, 5.4,
             5.6, 5.1, 4.5, 4.2, 4.3, 5.0, 5.9, 6.1, 5.8, 5.3, 4.7, 3.9]
_SUNDAY = [3.0, 2.5, 2.2, 2.0, 1.9, 1.9, 2.1, 2.7, 3.9, 5.0, 5.6, 6.2,
           6.4, 5.4, 4.6, 4.2, 4.3, 4.9, 5.8, 6.0, 5.7, 5.1, 4.4, 3.6]


def _dynamization(day_of_year: np.ndarray) -> np.ndarray:
    """BDEW dynamisation function: winter ~+24 %, summer ~-20 %."""
    d = day_of_year.astype(float)
    return -3.92e-10 * d**4 + 3.2e-7 * d**3 - 7.02e-5 * d**2 + 2.1e-3 * d + 1.24


def _shape(local: pd.DatetimeIndex) -> np.ndarray:
    profiles = np.array([_WEEKDAY, _SATURDAY, _SUNDAY])
    # weekday 0, Saturday 1, Sunday 2
    daytype = np.select([local.dayofweek == 5, local.dayofweek == 6], [1, 2], 0)
    return profiles[daytype, local.hour] * _dynamization(local.dayofyear.to_numpy())


# Normalisation: mean shape value over a whole year (computed once)
_YEAR = pd.date_range("2026-01-01", "2027-01-01", freq="15min", inclusive="left", tz=TZ)
_MEAN_SHAPE = float(_shape(_YEAR).mean())


def forecast(index: pd.DatetimeIndex, annual_kwh: float) -> pd.Series:
    """Expected load in kW for each point in time (index: UTC, 15 minute grid)."""
    shape = _shape(index.tz_convert(TZ))
    mean_kw = annual_kwh / 8760
    return pd.Series(shape / _MEAN_SHAPE * mean_kw, index=index)


def actual(index: pd.DatetimeIndex, annual_kwh: float, seed: int = 7) -> pd.Series:
    """Synthetic "actual load": forecast × autocorrelated, log-normal deviation (σ ≈ 30 %)."""
    rng = np.random.default_rng(seed + int(index[0].timestamp()) // 86400)
    noise = np.empty(len(index))
    e = 0.0
    for i in range(len(index)):
        e = 0.85 * e + rng.normal(0, 0.16)
        noise[i] = e
    return forecast(index, annual_kwh) * np.exp(noise - 0.5 * 0.3**2)
