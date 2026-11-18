"""Step 01 – data pipeline: load raw data and combine it into an hourly dataset.

Sources (all without API key):
  * energy-charts.info REST API (Fraunhofer ISE): day-ahead prices, actual generation/load,
    day-ahead forecasts (solar, wind, load), cross-border trade CH.
  * Open-Meteo Previous Runs API: weather forecasts as they were available on the previous day (no leakage).

Each year is loaded separately and cached under data/raw/; a repeated run
only loads missing years or the current year. Failures are listed at the end –
for these series the CSV has to be downloaded manually from the website (see SOURCES.txt).

Usage:  .venv/bin/python 01_fetch_data.py [--refresh]
"""

from __future__ import annotations

import json
import sys
import time

import pandas as pd
import requests

from common import (BIDDING_ZONES, FORECASTS, POWER_COUNTRIES, PROCESSED, RAW,
                    START, WEATHER_POINTS, WEATHER_VARS)

EC = "https://api.energy-charts.info"
OM = "https://previous-runs-api.open-meteo.com/v1/forecast"
NOW = pd.Timestamp.now(tz="UTC")
END = (NOW + pd.Timedelta(days=2)).normalize()
REFRESH = "--refresh" in sys.argv
PAUSE = 5.0  # seconds between requests (energy-charts throttles)
failures: list[str] = []


def get_json(url: str, params: dict) -> dict | None:
    """GET with backoff – energy-charts throttles with HTTP 429."""
    for attempt in range(8):
        try:
            r = requests.get(url, params=params, timeout=120)
        except requests.RequestException as exc:
            print(f"    Network error ({exc.__class__.__name__}), retrying …")
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            time.sleep(PAUSE)
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            wait = 30 * (attempt + 1)
            print(f"    HTTP {r.status_code}, waiting {wait}s …")
            time.sleep(wait)
            continue
        time.sleep(PAUSE)
        return None  # 4xx: series does not exist for this combination
    raise RuntimeError(f"{url} {params}: still throttled after several attempts – try again later")


def chunks() -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Yearly blocks – few, large requests spare the rate limit."""
    starts = pd.date_range(START, END, freq="YS", tz="UTC")
    return [(s, min(s + pd.offsets.YearBegin(1), END)) for s in starts]


def cached(name: str, start: pd.Timestamp, fetch) -> pd.DataFrame | None:
    """Read a yearly chunk from the cache or load it. The current year is always reloaded."""
    path = RAW / name / f"{start:%Y}.csv"
    current = start + pd.offsets.YearBegin(1) > NOW
    if path.exists() and not (REFRESH or current):
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    df = fetch()
    if df is None or df.empty:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def series(name: str, fetch_month) -> pd.DataFrame | None:
    """Load all years of a series and concatenate them."""
    print(f"  {name}")
    parts = []
    for start, end in chunks():
        df = cached(name, start, lambda: fetch_month(start, end))
        if df is not None:
            parts.append(df)
    if not parts:
        failures.append(name)
        return None
    df = pd.concat(parts)
    return df[~df.index.duplicated(keep="last")].sort_index()


def ec_frame(unix: list[int], columns: dict[str, list]) -> pd.DataFrame:
    idx = pd.to_datetime(unix, unit="s", utc=True)
    return pd.DataFrame(columns, index=idx).apply(pd.to_numeric, errors="coerce")


def ec_dates(start: pd.Timestamp, end: pd.Timestamp) -> dict:
    # energy-charts interprets plain dates as local time; ISO UTC timestamps are unambiguous.
    return {"start": start.strftime("%Y-%m-%dT%H:%MZ"), "end": (end - pd.Timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%MZ")}


def fetch_price(zone: str):
    def run(start, end):
        d = get_json(f"{EC}/price", {"bzn": zone, **ec_dates(start, end)})
        if not d or not d.get("unix_seconds"):
            return None
        return ec_frame(d["unix_seconds"], {"price": d["price"]})
    return run


def fetch_power(country: str):
    def run(start, end):
        d = get_json(f"{EC}/public_power", {"country": country, **ec_dates(start, end)})
        if not d or not d.get("unix_seconds"):
            return None
        return ec_frame(d["unix_seconds"], {p["name"]: p["data"] for p in d["production_types"]})
    return run


def fetch_forecast(country: str, kind: str):
    def run(start, end):
        d = get_json(f"{EC}/public_power_forecast",
                     {"country": country, "production_type": kind, "forecast_type": "day-ahead", **ec_dates(start, end)})
        if not d or not d.get("unix_seconds") or not d.get("forecast_values"):
            return None
        return ec_frame(d["unix_seconds"], {"forecast": d["forecast_values"]})
    return run


def fetch_cbet(country: str):
    def run(start, end):
        d = get_json(f"{EC}/cbet", {"country": country, **ec_dates(start, end)})
        if not d or not d.get("unix_seconds"):
            return None
        return ec_frame(d["unix_seconds"], {c["name"]: c["data"] for c in d["countries"]})
    return run


def fetch_weather(lat: float, lon: float):
    def run(start, end):
        last = min(end - pd.Timedelta(days=1), NOW.normalize() + pd.Timedelta(days=1))
        if last < start:
            return None
        hourly = ",".join(f"{v}_previous_day1" for v in WEATHER_VARS)
        d = get_json(OM, {"latitude": lat, "longitude": lon, "hourly": hourly, "timezone": "UTC",
                          "start_date": f"{start:%Y-%m-%d}", "end_date": f"{last:%Y-%m-%d}"})
        if not d or "hourly" not in d:
            return None
        h = d["hourly"]
        idx = pd.to_datetime(h.pop("time"), utc=True)
        return pd.DataFrame({k.removesuffix("_previous_day1"): v for k, v in h.items()}, index=idx)
    return run


def hourly(df: pd.DataFrame) -> pd.DataFrame:
    """Convert 15 min or hourly values to hourly means (prices from 10/2025 in 15 min)."""
    return df.resample("1h").mean()


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    cols: dict[str, pd.Series] = {}
    print(f"Period {START:%Y-%m-%d} – {END:%Y-%m-%d}")

    print("Day-ahead prices (EUR/MWh)")
    for key, zone in BIDDING_ZONES.items():
        df = series(f"price_{key}", fetch_price(zone))
        if df is not None:
            df.to_csv(PROCESSED / f"price_{key}_native.csv")  # original resolution (15/60 min)
            cols[f"price_{key}"] = hourly(df)["price"]

    print("Actual generation and load (MW)")
    for c in POWER_COUNTRIES:
        df = series(f"power_{c}", fetch_power(c))
        if df is None:
            continue
        df = hourly(df)
        for name in df.columns:
            slug = name.lower().replace(" / ", "_").replace("-", "_").replace(" ", "_")
            cols[f"act_{c}_{slug}"] = df[name]

    print("Day-ahead forecasts (MW)")
    for c, kinds in FORECASTS.items():
        for kind in kinds:
            df = series(f"fc_{c}_{kind}", fetch_forecast(c, kind))
            if df is not None:
                cols[f"fc_{c}_{kind}"] = hourly(df)["forecast"]

    print("Cross-border trade CH (GW, + = import)")
    df = series("cbet_ch", fetch_cbet("ch"))
    if df is not None:
        for name in hourly(df).columns:
            cols[f"cbet_ch_{name.lower()}"] = hourly(df)[name]

    print("Previous-day weather forecasts (Open-Meteo)")
    for point, (lat, lon) in WEATHER_POINTS.items():
        df = series(f"weather_{point}", fetch_weather(lat, lon))
        if df is not None:
            for v in df.columns:
                cols[f"wx_{point}_{v}"] = hourly(df)[v]

    data = pd.DataFrame(cols).loc[START:END]
    data.index.name = "time_utc"
    data.to_csv(PROCESSED / "hourly.csv")
    meta = {"created_utc": NOW.isoformat(), "rows": len(data), "columns": list(data.columns),
            "failed_series": failures}
    (PROCESSED / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"\n{len(data)} hours × {data.shape[1]} columns → {PROCESSED / 'hourly.csv'}")
    if failures:
        print("Not available via API (download manually, see SOURCES.txt):")
        for f in failures:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
