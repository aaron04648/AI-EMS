"""PVGIS (EU JRC) – historische Stundenreihen und Jahresertrag.

Dokumentation: https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/getting-started-pvgis/api-non-interactive-service_en
Konvention Ausrichtung (aspect): 0 = Süd, -90 = Ost, 90 = West.
"""

from __future__ import annotations

import pandas as pd

from .http import get_json

BASE = "https://re.jrc.ec.europa.eu/api/v5_3"
YEARS = (2020, 2023)  # SARAH-3 deckt Europa bis 2023 ab
DAY = 24 * 3600


def _frame(hourly: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(hourly)
    df.index = pd.to_datetime(df.pop("time"), format="%Y%m%d:%H%M", utc=True)
    return df


def hourly_series(lat: float, lon: float, tilt: float, azimuth: float, loss: float) -> pd.DataFrame:
    """PV-Leistung (W pro kWp) der gewählten Ausrichtung + horizontale Einstrahlung (W/m²).

    Zwei Abfragen: einmal mit PV-Berechnung für die Anlage, einmal die Strahlungskomponenten
    auf der Horizontalen (= Globalstrahlung, wie sie auch die Wetterprognose liefert).
    """
    common = {
        "lat": round(lat, 3),
        "lon": round(lon, 3),
        "startyear": YEARS[0],
        "endyear": YEARS[1],
        "outputformat": "json",
    }
    pv = get_json(
        f"{BASE}/seriescalc",
        {**common, "pvcalculation": 1, "peakpower": 1, "loss": round(loss, 1),
         "angle": round(tilt), "aspect": round(azimuth)},
        ttl=7 * DAY,
    )
    horizontal = get_json(
        f"{BASE}/seriescalc",
        {**common, "pvcalculation": 0, "angle": 0, "aspect": 0, "components": 1},
        ttl=7 * DAY,
    )
    p = _frame(pv["outputs"]["hourly"])
    h = _frame(horizontal["outputs"]["hourly"])
    ghi = h["Gb(i)"] + h["Gd(i)"] + h["Gr(i)"]
    return pd.DataFrame(
        {"p_w_per_kwp": p["P"], "ghi": ghi, "sun_elevation": p["H_sun"], "temp": p["T2m"]}
    ).dropna()


def yearly_yield(lat: float, lon: float, tilt: float, azimuth: float, loss: float, kwp: float) -> dict:
    """Langjähriger mittlerer Ertrag (kWh) – gesamt und pro Monat."""
    data = get_json(
        f"{BASE}/PVcalc",
        {"lat": round(lat, 3), "lon": round(lon, 3), "peakpower": round(kwp, 2), "loss": round(loss, 1),
         "angle": round(tilt), "aspect": round(azimuth), "outputformat": "json"},
        ttl=7 * DAY,
    )
    fixed = data["outputs"]
    return {
        "annual_kwh": fixed["totals"]["fixed"]["E_y"],
        "monthly_kwh": [m["E_m"] for m in fixed["monthly"]["fixed"]],
    }
