"""Statistisches PV-Prognosemodell.

Analog zum LightGBM-Modell im Paper (Ruddick et al. 2024, Abschnitt 3.5.3) wird ein
Gradient-Boosting-Regressor trainiert – hier jedoch mit Wetterdaten als Eingang, wie gewünscht:

    Merkmale:  Globalstrahlung (horizontal), Sonnenhöhe, Sonnenazimut, Lufttemperatur
    Ziel:      PV-Leistung der konkreten Anlage (W pro kWp)

Trainingsdaten sind die PVGIS-Stundenreihen 2020–2023 am Standort (SARAH-3-Satellitendaten).
Das Modell lernt so die Umrechnung von horizontaler Einstrahlung auf die geneigte, ausgerichtete
Modulfläche inklusive Temperatur- und Systemverlusten. Angewendet wird es auf die MeteoSwiss-
Prognose (ICON-CH), die dieselben Grössen liefert.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

from ..sources import pvgis
from .solar import sun_position

FEATURES = ["ghi", "sun_elevation", "sun_azimuth", "temp"]


@dataclass
class PvModel:
    model: HistGradientBoostingRegressor
    r2: float
    mae_w_per_kwp: float
    train_rows: int
    test_year: int

    def predict(self, weather: pd.DataFrame, lat: float, lon: float, kwp: float) -> pd.Series:
        """PV-Leistung in kW für stündliche Wetterwerte (Strahlung = Mittel der Vorstunde)."""
        # Sonnenstand zur Intervallmitte (Zeitstempel markiert das Ende der Stunde)
        elevation, azimuth = sun_position(weather.index - pd.Timedelta(minutes=30), lat, lon)
        x = pd.DataFrame({
            "ghi": weather["ghi"].to_numpy(),
            "sun_elevation": elevation,
            "sun_azimuth": azimuth,
            "temp": weather["temp"].to_numpy(),
        })
        p = self.model.predict(x[FEATURES])
        p = np.where((elevation <= 0) | (x["ghi"].to_numpy() <= 0), 0.0, np.clip(p, 0, None))
        return pd.Series(p * kwp / 1000, index=weather.index)


_cache: dict[tuple, PvModel] = {}
_lock = threading.Lock()


def get_model(lat: float, lon: float, tilt: float, azimuth: float, loss: float) -> PvModel:
    key = (round(lat, 2), round(lon, 2), round(tilt), round(azimuth), round(loss))
    with _lock:
        if key in _cache:
            return _cache[key]

    df = pvgis.hourly_series(lat, lon, tilt, azimuth, loss)
    df = df[df["sun_elevation"] > 0].copy()
    # PVGIS-Werte gelten für die Stunde, beginnend beim Zeitstempel (hh:10) → Sonnenstand zur Mitte
    _, df["sun_azimuth"] = sun_position(df.index + pd.Timedelta(minutes=20), lat, lon)

    test_year = df.index.year.max()
    train = df[df.index.year < test_year]
    test = df[df.index.year == test_year]

    model = HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.08, max_leaf_nodes=48, min_samples_leaf=20, random_state=42
    )
    model.fit(train[FEATURES], train["p_w_per_kwp"])
    pred = model.predict(test[FEATURES])
    result = PvModel(
        model=model,
        r2=float(r2_score(test["p_w_per_kwp"], pred)),
        mae_w_per_kwp=float(mean_absolute_error(test["p_w_per_kwp"], pred)),
        train_rows=len(train),
        test_year=int(test_year),
    )
    # Nach dem Test auf allen Jahren nachtrainieren
    model.fit(df[FEATURES], df["p_w_per_kwp"])

    with _lock:
        _cache[key] = result
        if len(_cache) > 32:
            _cache.pop(next(iter(_cache)))
    return result
