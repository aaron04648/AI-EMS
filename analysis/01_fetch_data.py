"""Schritt 01 – Datenpipeline: Rohdaten laden und zu einem stündlichen Datensatz vereinen.

Quellen (alle ohne API-Key):
  * energy-charts.info REST-API (Fraunhofer ISE): Day-Ahead-Preise, Ist-Erzeugung/Last,
    Day-Ahead-Prognosen (Solar, Wind, Last), grenzüberschreitender Handel CH.
  * Open-Meteo Previous-Runs-API: Wetterprognosen, wie sie am Vortag vorlagen (kein Leakage).

Jedes Jahr wird einzeln geladen und unter data/raw/ zwischengespeichert; ein erneuter Lauf
lädt nur fehlende bzw. das laufende Jahr nach. Fehlschläge werden am Ende aufgelistet –
für diese Reihen muss die CSV manuell von der Website geladen werden (siehe QUELLEN.txt).

Aufruf:  .venv/bin/python 01_fetch_data.py [--refresh]
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
PAUSE = 5.0  # Sekunden zwischen Anfragen (energy-charts drosselt)
failures: list[str] = []


def get_json(url: str, params: dict) -> dict | None:
    """GET mit Backoff – energy-charts drosselt mit HTTP 429."""
    for attempt in range(8):
        try:
            r = requests.get(url, params=params, timeout=120)
        except requests.RequestException as exc:
            print(f"    Netzwerkfehler ({exc.__class__.__name__}), neuer Versuch …")
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            time.sleep(PAUSE)
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            wait = 30 * (attempt + 1)
            print(f"    HTTP {r.status_code}, warte {wait}s …")
            time.sleep(wait)
            continue
        time.sleep(PAUSE)
        return None  # 4xx: Reihe existiert für diese Kombination nicht
    raise RuntimeError(f"{url} {params}: nach mehreren Versuchen weiterhin gedrosselt – später erneut starten")


def chunks() -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Jahresblöcke – wenige, grosse Anfragen schonen das Rate-Limit."""
    starts = pd.date_range(START, END, freq="YS", tz="UTC")
    return [(s, min(s + pd.offsets.YearBegin(1), END)) for s in starts]


def cached(name: str, start: pd.Timestamp, fetch) -> pd.DataFrame | None:
    """Jahres-Chunk aus dem Cache lesen oder laden. Das laufende Jahr wird immer neu geladen."""
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
    """Alle Jahre einer Reihe laden und aneinanderhängen."""
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
    # energy-charts interpretiert reine Daten als Lokalzeit; mit ISO-UTC-Zeitstempeln eindeutig.
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
    """15-min- bzw. Stundenwerte auf Stundenmittel bringen (Preise ab 10/2025 in 15 min)."""
    return df.resample("1h").mean()


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    cols: dict[str, pd.Series] = {}
    print(f"Zeitraum {START:%Y-%m-%d} – {END:%Y-%m-%d}")

    print("Day-Ahead-Preise (EUR/MWh)")
    for key, zone in BIDDING_ZONES.items():
        df = series(f"price_{key}", fetch_price(zone))
        if df is not None:
            df.to_csv(PROCESSED / f"price_{key}_native.csv")  # Originalauflösung (15/60 min)
            cols[f"price_{key}"] = hourly(df)["price"]

    print("Ist-Erzeugung und Last (MW)")
    for c in POWER_COUNTRIES:
        df = series(f"power_{c}", fetch_power(c))
        if df is None:
            continue
        df = hourly(df)
        for name in df.columns:
            slug = name.lower().replace(" / ", "_").replace("-", "_").replace(" ", "_")
            cols[f"act_{c}_{slug}"] = df[name]

    print("Day-Ahead-Prognosen (MW)")
    for c, kinds in FORECASTS.items():
        for kind in kinds:
            df = series(f"fc_{c}_{kind}", fetch_forecast(c, kind))
            if df is not None:
                cols[f"fc_{c}_{kind}"] = hourly(df)["forecast"]

    print("Grenzüberschreitender Handel CH (GW, + = Import)")
    df = series("cbet_ch", fetch_cbet("ch"))
    if df is not None:
        for name in hourly(df).columns:
            cols[f"cbet_ch_{name.lower()}"] = hourly(df)[name]

    print("Wetterprognosen Vortag (Open-Meteo)")
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
    print(f"\n{len(data)} Stunden × {data.shape[1]} Spalten → {PROCESSED / 'hourly.csv'}")
    if failures:
        print("Nicht per API verfügbar (manuell laden, siehe QUELLEN.txt):")
        for f in failures:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
