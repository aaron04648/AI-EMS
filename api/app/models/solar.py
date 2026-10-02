"""Sonnenstand (NOAA-Näherung, Genauigkeit ~0.1°) – vektorisiert für pandas-Zeitindizes."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sun_position(times: pd.DatetimeIndex, lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
    """Liefert (Elevation, Azimut) in Grad. Azimut-Konvention wie PVGIS: 0 = Süd, -90 = Ost, 90 = West."""
    t = times.tz_convert("UTC")
    doy = t.dayofyear.to_numpy()
    hour = (t.hour + t.minute / 60 + t.second / 3600).to_numpy()

    gamma = 2 * np.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (
        0.000075 + 0.001868 * np.cos(gamma) - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma) - 0.040849 * np.sin(2 * gamma)
    )
    decl = (
        0.006918 - 0.399912 * np.cos(gamma) + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma) + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma) + 0.00148 * np.sin(3 * gamma)
    )
    true_solar_minutes = hour * 60 + eqtime + 4 * lon
    hour_angle = np.radians(true_solar_minutes / 4 - 180)

    phi = np.radians(lat)
    cos_zenith = np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.cos(hour_angle)
    zenith = np.arccos(np.clip(cos_zenith, -1, 1))
    elevation = 90 - np.degrees(zenith)

    # Azimut ab Süd, positiv nach Westen
    azimuth = np.degrees(
        np.arctan2(np.sin(hour_angle), np.cos(hour_angle) * np.sin(phi) - np.tan(decl) * np.cos(phi))
    )
    return elevation, azimuth
