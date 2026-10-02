"""Wetterdaten von MeteoSwiss.

- Prognosen: Modelle ICON-CH1 (1 km, ~33 h) und ICON-CH2 (2 km, 5 Tage). MeteoSwiss publiziert
  sie als Open Government Data (GRIB2); wir beziehen sie über Open-Meteo als JSON – inklusive
  der Läufe vom Vortag („previous runs“).
- Messungen: SwissMetNet-Stationen (Globalstrahlung gre000h0, Temperatur tre200h0) direkt aus
  den MeteoSwiss-Open-Data (data.geo.admin.ch, STAC-Kollektion ch.meteoschweiz.ogd-smn).

Der Backtest ist damit ehrlich: Die MPC sieht für jeden Tag die Prognose vom Vortag,
abgerechnet wird mit der Messung der nächstgelegenen Station.

Lizenz: MeteoSwiss Open Data, CC BY 4.0 (https://opendatadocs.meteoswiss.ch).
"""

from __future__ import annotations

import io
import math

import pandas as pd

from .http import get_json, get_text

SMN = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn"

FORECAST = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
MODELS = ("meteoswiss_icon_ch1", "meteoswiss_icon_ch2")


def _hourly_frame(hourly: dict) -> pd.DataFrame:
    df = pd.DataFrame(hourly)
    # Open-Meteo liefert UTC-Zeitstempel; Strahlung = Mittel der vorangehenden Stunde
    df.index = pd.to_datetime(df.pop("time"), utc=True)
    return df


def forecast(lat: float, lon: float, days: int = 2) -> pd.DataFrame:
    """Aktuelle Prognose: ICON-CH1, wo vorhanden, sonst ICON-CH2. Spalten: ghi, temp."""
    data = get_json(
        FORECAST,
        {"latitude": round(lat, 3), "longitude": round(lon, 3),
         "hourly": "shortwave_radiation,temperature_2m", "models": ",".join(MODELS),
         "forecast_days": days + 1, "past_days": 1, "timezone": "GMT"},
        ttl=900,
    )
    df = _hourly_frame(data["hourly"])
    ghi = df["shortwave_radiation_meteoswiss_icon_ch1"].fillna(df["shortwave_radiation_meteoswiss_icon_ch2"])
    temp = df["temperature_2m_meteoswiss_icon_ch1"].fillna(df["temperature_2m_meteoswiss_icon_ch2"])
    return pd.DataFrame({"ghi": ghi, "temp": temp}).dropna()


def nearest_station(lat: float, lon: float) -> dict:
    """Nächstgelegene aktive SwissMetNet-Station, die Globalstrahlung misst."""
    inventory = pd.read_csv(io.StringIO(get_text(f"{SMN}/ogd-smn_meta_datainventory.csv", 86400, "latin-1")), sep=";")
    active = inventory[(inventory["parameter_shortname"] == "gre000h0") & inventory["data_till"].isna()]
    stations = pd.read_csv(io.StringIO(get_text(f"{SMN}/ogd-smn_meta_stations.csv", 86400, "latin-1")), sep=";")
    stations = stations[stations["station_abbr"].isin(active["station_abbr"])].copy()
    # Distanz (Haversine) in km
    p1, p2 = math.radians(lat), stations["station_coordinates_wgs84_lat"].map(math.radians)
    dphi = p2 - p1
    dlmb = (stations["station_coordinates_wgs84_lon"] - lon).map(math.radians)
    a = (dphi / 2).map(math.sin) ** 2 + math.cos(p1) * p2.map(math.cos) * (dlmb / 2).map(math.sin) ** 2
    stations["km"] = 2 * 6371 * a.map(lambda v: math.asin(math.sqrt(v)))
    best = stations.sort_values("km").iloc[0]
    return {
        "abbr": best["station_abbr"],
        "name": best["station_name"],
        "km": round(float(best["km"]), 1),
        "height": float(best["station_height_masl"]),
    }


def station_measurements(abbr: str) -> pd.DataFrame:
    """Stundenwerte der Station (letzte Wochen + heute). Spalten: ghi, temp; Index UTC (Ende der Stunde)."""
    frames = []
    for part in ("recent", "now"):
        url = f"{SMN}/{abbr.lower()}/ogd-smn_{abbr.lower()}_h_{part}.csv"
        df = pd.read_csv(io.StringIO(get_text(url, 1800, "latin-1")), sep=";")
        df.index = pd.to_datetime(df["reference_timestamp"], format="%d.%m.%Y %H:%M", utc=True)
        frames.append(df[["gre000h0", "tre200h0"]])
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.rename(columns={"gre000h0": "ghi", "tre200h0": "temp"})


def backtest(lat: float, lon: float, past_days: int) -> tuple[pd.DataFrame, dict]:
    """Vergangene Tage: Messung der nächsten MeteoSwiss-Station (Ist) und ICON-CH2-Prognose vom Vortag.

    Spalten: ghi, temp (Ist) sowie ghi_fc, temp_fc (Prognose vom Vortag am Standort).
    """
    station = nearest_station(lat, lon)
    measured = station_measurements(station["abbr"])
    data = get_json(
        PREVIOUS_RUNS,
        {"latitude": round(lat, 3), "longitude": round(lon, 3),
         "hourly": "shortwave_radiation,shortwave_radiation_previous_day1,"
                   "temperature_2m,temperature_2m_previous_day1",
         "models": MODELS[1], "past_days": past_days + 1, "forecast_days": 1, "timezone": "GMT"},
        ttl=3600,
    )
    df = _hourly_frame(data["hourly"])
    out = pd.DataFrame({
        "ghi_fc": df["shortwave_radiation_previous_day1"],
        "temp_fc": df["temperature_2m_previous_day1"],
    })
    # Messlücken der Station mit dem jüngsten Modelllauf überbrücken
    out["ghi"] = measured["ghi"].reindex(out.index).fillna(df["shortwave_radiation"])
    out["temp"] = measured["temp"].reindex(out.index).fillna(df["temperature_2m"])
    return out.dropna(), station
