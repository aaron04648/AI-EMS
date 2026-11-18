"""Weather data from MeteoSwiss.

- Forecasts: models ICON-CH1 (1 km, ~33 h) and ICON-CH2 (2 km, 5 days). MeteoSwiss publishes
  them as Open Government Data (GRIB2); we obtain them via Open-Meteo as JSON – including
  the runs from the previous day ("previous runs").
- Measurements: SwissMetNet stations (global irradiance gre000h0, temperature tre200h0) directly from
  the MeteoSwiss Open Data (data.geo.admin.ch, STAC collection ch.meteoschweiz.ogd-smn).

This keeps the backtest honest: for each day the MPC sees the forecast from the previous day,
billing uses the measurement of the nearest station.

License: MeteoSwiss Open Data, CC BY 4.0 (https://opendatadocs.meteoswiss.ch).
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
    # Open-Meteo returns UTC timestamps; irradiance = mean of the preceding hour
    df.index = pd.to_datetime(df.pop("time"), utc=True)
    return df


def forecast(lat: float, lon: float, days: int = 2) -> pd.DataFrame:
    """Current forecast: ICON-CH1 where available, otherwise ICON-CH2. Columns: ghi, temp."""
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
    """Nearest active SwissMetNet station that measures global irradiance."""
    inventory = pd.read_csv(io.StringIO(get_text(f"{SMN}/ogd-smn_meta_datainventory.csv", 86400, "latin-1")), sep=";")
    active = inventory[(inventory["parameter_shortname"] == "gre000h0") & inventory["data_till"].isna()]
    stations = pd.read_csv(io.StringIO(get_text(f"{SMN}/ogd-smn_meta_stations.csv", 86400, "latin-1")), sep=";")
    stations = stations[stations["station_abbr"].isin(active["station_abbr"])].copy()
    # Distance (haversine) in km
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
    """Hourly values of the station (last weeks + today). Columns: ghi, temp; index UTC (end of the hour)."""
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
    """Past days: measurement of the nearest MeteoSwiss station (actual) and ICON-CH2 forecast from the previous day.

    Columns: ghi, temp (actual) as well as ghi_fc, temp_fc (forecast from the previous day at the location).
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
    # Bridge measurement gaps of the station with the latest model run
    out["ghi"] = measured["ghi"].reindex(out.index).fillna(df["shortwave_radiation"])
    out["temp"] = measured["temp"].reindex(out.index).fillna(df["temperature_2m"])
    return out.dropna(), station


# Grid over Switzerland for the weather map (ICON-CH2 covers the entire Alpine region)
GRID_LAT = (45.75, 47.85)
GRID_LON = (5.85, 10.55)
GRID_STEP = 0.15


def weather_grid(hours_back: int = 6, hours_ahead: int = 48) -> dict:
    """Cloud cover (%), precipitation (mm/h) and global irradiance (W/m²) as a grid per hour.

    One request with all grid points (Open-Meteo allows coordinate lists), cached for 30 min.
    """
    n_lat = round((GRID_LAT[1] - GRID_LAT[0]) / GRID_STEP) + 1
    n_lon = round((GRID_LON[1] - GRID_LON[0]) / GRID_STEP) + 1
    lats = [round(GRID_LAT[0] + i * GRID_STEP, 3) for i in range(n_lat)]
    lons = [round(GRID_LON[0] + j * GRID_STEP, 3) for j in range(n_lon)]
    data = get_json(
        FORECAST,
        {"latitude": ",".join(str(a) for a in lats for _ in lons),
         "longitude": ",".join(str(b) for _ in lats for b in lons),
         "hourly": "cloud_cover,precipitation,shortwave_radiation",
         "models": MODELS[1], "past_days": 1, "forecast_days": 3, "timezone": "GMT"},
        ttl=1800,
    )
    times = pd.to_datetime(data[0]["hourly"]["time"], utc=True)
    now = pd.Timestamp.now(tz="UTC").floor("h")
    keep = [i for i, t in enumerate(times)
            if now - pd.Timedelta(hours=hours_back) <= t <= now + pd.Timedelta(hours=hours_ahead)]

    def field(name: str, digits: int) -> list:
        # [time][latitude][longitude], latitudes from south to north
        out = []
        for i in keep:
            frame = []
            for a in range(n_lat):
                row = []
                for b in range(n_lon):
                    v = data[a * n_lon + b]["hourly"][name][i]
                    row.append(None if v is None else round(v, digits) if digits else round(v))
                frame.append(row)
            out.append(frame)
        return out

    return {
        "lats": lats,
        "lons": lons,
        "times": [int(times[i].timestamp()) for i in keep],
        "now": int(now.timestamp()),
        "model": "MeteoSwiss ICON-CH2",
        "cloud": field("cloud_cover", 0),
        "precip": field("precipitation", 1),
        "radiation": field("shortwave_radiation", 0),
    }
