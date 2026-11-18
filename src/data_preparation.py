"""Data preparation for the 48 h profile (24 h known + 24 h forecast).

Forecast situation (D+2):
    On day D, after the Swiss auction for D+1 has been published (~12:00), every hour t of D+2 is
    forecast. At that time the following is known:
      * all Swiss and neighbouring prices up to the end of D+1  → price lags ≥ 24 h
      * actual generation and trade (ENTSO-E) up to early D      → actuals only with lag ≥ 72 h
      * gas and CO2 price of day D                               → daily value shifted by 2 days
      * reservoir level (SFOE, weekly, published with delay)     → level of ≥ 7 days ago
      * weather forecasts for D+2 (Open-Meteo, run of day D)     → used directly as predictors

The TSO forecasts (solar, wind, load) are only published in the evening of D+1 and are unknown at
forecast time. Instead, the weather forecasts that already existed on day D go directly into the
price model.

Sources:
  * analysis/01_fetch_data.py: prices, actual generation, TSO forecasts (energy-charts API, hourly
    from 2024). If the file is missing, `load_hourly` starts the download (~30 min).
  * Open-Meteo Previous Runs API: weather forecasts as they were issued two or three days before.
  * energy-charts.info chart data (not in the API): Swiss reservoir level, gas/CO2 prices.
All downloads are cached under data/raw/.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import holidays
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
HOURLY_CSV = ROOT / "analysis" / "data" / "processed" / "hourly.csv"
RAW = ROOT / "data" / "raw" / "energy_charts_web"
RAW_WEATHER = ROOT / "data" / "raw" / "open_meteo"
TZ = "Europe/Zurich"
START = pd.Timestamp("2024-01-01", tz="UTC")
CHARTS = "https://www.energy-charts.info/charts"
OPEN_METEO = "https://previous-runs-api.open-meteo.com/v1/forecast"

TARGET = "Price CH"
TARGET_DELTA = "Δ Price CH vs previous day"

COUNTRIES = {"ch": "CH", "de": "DE", "fr": "FR", "it": "IT", "at": "AT"}

# Weather points per country: cities (load, solar) and wind regions
WEATHER_POINTS: dict[str, dict[str, tuple[float, float]]] = {
    "ch": {"zurich": (47.377, 8.540), "bern": (46.948, 7.447), "geneva": (46.204, 6.143),
           "lugano": (46.004, 8.951), "sion": (46.233, 7.360)},
    "de": {"hamburg": (53.551, 9.994), "berlin": (52.520, 13.405), "munich": (48.137, 11.575),
           "frankfurt": (50.110, 8.682), "dortmund": (51.514, 7.468), "leipzig": (51.340, 12.375),
           "stuttgart": (48.776, 9.183), "husum": (54.480, 9.050), "north_sea": (54.400, 6.500)},
    "fr": {"paris": (48.857, 2.352), "lyon": (45.764, 4.836), "toulouse": (43.605, 1.444),
           "marseille": (43.297, 5.370), "nantes": (47.218, -1.554), "lille": (50.629, 3.057),
           "strasbourg": (48.573, 7.752), "montpellier": (43.611, 3.877)},
    "it": {"milan": (45.464, 9.190), "turin": (45.070, 7.687), "bologna": (44.494, 11.343),
           "venice": (45.441, 12.316), "rome": (41.903, 12.496), "naples": (40.852, 14.268),
           "foggia": (41.462, 15.545), "palermo": (38.116, 13.361)},
    "at": {"vienna": (48.208, 16.373), "linz": (48.306, 14.286), "graz": (47.071, 15.440),
           "innsbruck": (47.269, 11.404), "parndorf": (47.997, 16.860)},
}
WEATHER_VARS = ["temperature_2m", "shortwave_radiation", "cloud_cover", "wind_speed_100m", "precipitation"]

# Up to this UTC hour of D+2, the run from two days before is already available at ~12:00 on day D
# (run at h UTC on day D, published ~4 h later). Later hours use the run from three days before.
DAY2_MAX_UTC_HOUR = 6


# --------------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------------
def load_hourly(path: Path = HOURLY_CSV) -> pd.DataFrame:
    """Hourly data set from analysis/01_fetch_data.py (index UTC).

    If the file is missing (e.g. freshly cloned repo), the download is started (~30 min, the
    energy-charts API throttles requests). Afterwards everything is cached.
    """
    if not path.exists():
        script = ROOT / "analysis" / "01_fetch_data.py"
        venv = ROOT / "analysis" / ".venv" / "bin" / "python"
        python = str(venv) if venv.exists() else sys.executable
        print(f"{path.relative_to(ROOT)} is missing – starting {script.relative_to(ROOT)} (~30 min) …")
        subprocess.run([python, script.name], cwd=script.parent, check=True)
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.loc[START:]


def _fetch_chart(chart: str, file: str, cache: Path) -> list[dict]:
    """Load one chart JSON file from energy-charts.info (cached, with backoff on 429/5xx)."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    url = f"{CHARTS}/{chart}/data/{file}"
    for attempt in range(5):
        r = requests.get(url, timeout=60)
        time.sleep(1)
        if r.status_code not in (429, 500, 502, 503, 504):
            break
        time.sleep(10 * (attempt + 1))
    r.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(r.text, encoding="utf-8")
    return r.json()


def _name(series: dict) -> str:
    # The chart files carry German and English names; the German ones are used as stable keys.
    n = series["name"]
    if isinstance(n, list):
        n = n[0]
    return n.get("de", n.get("en")) if isinstance(n, dict) else str(n)


def _frame(series: list[dict]) -> pd.DataFrame:
    index = pd.to_datetime(series[0]["xAxisValues"], unit="ms", utc=True)
    return pd.DataFrame({_name(s): s["data"] for s in series}, index=index).apply(pd.to_numeric, errors="coerce")


def storage_week(index: pd.DatetimeIndex) -> np.ndarray:
    """Week of the year 1–52, derived from the day of the year.

    Not the ISO week: it often assigns the end of December to week 1 of the following year, so a
    December value would be compared with the January mean.
    """
    return np.minimum((index.tz_convert(TZ).day_of_year.to_numpy() - 1) // 7 + 1, 52)


def load_storage_ch(years=range(2015, 2027)) -> pd.DataFrame:
    """Weekly filling level of the Swiss storage lakes (SFOE via energy-charts), in TWh and %.

    `Reservoir level anomaly` = level in % minus the mean of the same week 2015–2023. The level
    itself is strongly seasonal; only the deviation shows whether more or less water than usual is
    stored (scarcity → higher prices in winter).
    """
    parts = [_frame(_fetch_chart("filling_level", f"ch/year_storage_{y}.json", RAW / "storage_ch" / f"{y}.json"))
             for y in years]
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    regions = ["Wallis", "Graubünden", "Tessin", "Übrige Schweiz"]  # series names in the source file
    out = pd.DataFrame(index=df.index)
    out["Reservoir level TWh"] = df[regions].sum(axis=1, min_count=len(regions))
    out["Reservoir level %"] = 100 * out["Reservoir level TWh"] / df["Ganze Schweiz max. Speicher"]
    week = storage_week(out.index)
    clim = out.loc[out.index.year <= 2023, "Reservoir level %"].groupby(week[out.index.year <= 2023]).mean()
    out["Reservoir level anomaly"] = out["Reservoir level %"] - clim.reindex(week).to_numpy()
    return out.dropna()


EC_API = "https://api.energy-charts.info"
WARMUP_START = pd.Timestamp("2023-12-01", tz="UTC")
DATASET_CSV = ROOT / "data" / "processed" / "candidates_d2.csv"


def _slug(name: str) -> str:
    return name.lower().replace(" / ", "_").replace("-", "_").replace(" ", "_")


def load_warmup_2023() -> pd.DataFrame:
    """December 2023 for the columns the lags are computed from (same names as `load_hourly`).

    Without this lead-in, the first hours of 2024 would be empty: the 168 h lag needs the week
    before, and the actuals with a 72 h lag need the last three days of 2023. Same source as
    analysis/01_fetch_data.py.
    """
    cache = ROOT / "data" / "raw" / "energy_charts_api" / "warmup_2023-12.csv"
    if cache.exists():
        df = pd.read_csv(cache, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    period = {"start": f"{WARMUP_START:%Y-%m-%dT%H:%MZ}", "end": "2023-12-31T23:59Z"}

    def get(endpoint: str, **params) -> dict:
        for attempt in range(6):
            r = requests.get(f"{EC_API}/{endpoint}", params={**params, **period}, timeout=120)
            time.sleep(5)  # energy-charts throttles requests
            if r.status_code == 200:
                return r.json()
            time.sleep(30 * (attempt + 1))
        r.raise_for_status()

    def frame(d: dict, cols: dict) -> pd.DataFrame:
        idx = pd.to_datetime(d["unix_seconds"], unit="s", utc=True)
        return pd.DataFrame(cols, index=idx).apply(pd.to_numeric, errors="coerce").resample("1h").mean()

    cols = {}
    for key, zone in {"ch": "CH", "de": "DE-LU", "fr": "FR", "it": "IT-North", "at": "AT"}.items():
        d = get("price", bzn=zone)
        cols[f"price_{key}"] = frame(d, {"p": d["price"]})["p"]
    for country in ("ch", "fr"):
        d = get("public_power", country=country)
        power = frame(d, {p["name"]: p["data"] for p in d["production_types"]})
        for name in power:
            cols[f"act_{country}_{_slug(name)}"] = power[name]
    d = get("cbet", country="ch")
    trade = frame(d, {c["name"]: c["data"] for c in d["countries"]})
    cols["cbet_ch_sum"] = trade["sum"] if "sum" in trade else trade.sum(axis=1)
    df = pd.DataFrame(cols)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache)
    return df


def load_fuel_prices(years=range(2023, 2027)) -> pd.DataFrame:
    """Daily gas (THE) and CO2 (EU auction) prices and the marginal-cost proxy of a gas plant (2×gas + 0.4×CO2)."""
    parts = [_frame(_fetch_chart("price_spot_market", f"de/year_{y}.json", RAW / "spot_market_de" / f"{y}.json"))
             for y in years]
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    cols = {"Gas (NCG, THE)": "Gas price THE", "CO2 Emissionszertifikate, Auktion EU": "CO2 price EU",
            "2 × Gas + 0,4 × CO2 (DE)": "Gas plant marginal cost"}  # keys = series names in the source file
    daily = df[list(cols)].rename(columns=cols)
    return daily.groupby(daily.index.tz_convert(TZ).date).mean()


# --------------------------------------------------------------------------------------------
# Weather forecasts for D+2 (Open-Meteo Previous Runs)
# --------------------------------------------------------------------------------------------
def _get_open_meteo(params: dict) -> list[dict]:
    """GET with retries; under load the service sometimes does not answer for minutes."""
    for attempt in range(6):
        try:
            r = requests.get(OPEN_METEO, params=params, timeout=600)
        except requests.RequestException as exc:
            print(f"    Open-Meteo: {exc.__class__.__name__}, retrying …")
            time.sleep(30 * (attempt + 1))
            continue
        if r.status_code == 200:
            time.sleep(5)  # respect the per-minute limit of the free API
            try:
                data = r.json()
            except ValueError:  # truncated response
                print("    Open-Meteo: incomplete response, retrying …", flush=True)
                time.sleep(30 * (attempt + 1))
                continue
            return data if isinstance(data, list) else [data]
        if r.status_code in (429, 500, 502, 503, 504):
            print(f"    Open-Meteo: HTTP {r.status_code}, waiting …")
            time.sleep(60 * (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError("Open-Meteo does not respond – try again later (the cache is kept).")


def _fetch_weather_half(start: pd.Timestamp, refresh: bool) -> pd.DataFrame:
    """Forecasts from 2 and 3 days before, for all points and one half-year.

    The service needs several minutes per request, hence few large requests. A half-year for 35
    points counts as ~450 calls at Open-Meteo and stays below the per-minute limit (600).
    Completed half-years are loaded once, the current one at most once per day.
    """
    today = pd.Timestamp.now(tz="UTC").normalize()
    last = start + pd.DateOffset(months=6) - pd.Timedelta(days=1)
    end = min(last, today + pd.Timedelta(days=1))
    complete = last < today
    cache = RAW_WEATHER / f"previous_runs_{start:%Y}H{1 if start.month == 1 else 2}.json"
    keys = [(c, p) for c, pts in WEATHER_POINTS.items() for p in pts]
    if cache.exists() and not refresh and (complete or cache.stat().st_mtime > time.time() - 86400):
        data = json.loads(cache.read_text(encoding="utf-8"))
    else:
        print(f"Open-Meteo: loading weather forecasts {start:%Y-%m} to {end:%Y-%m} for {len(keys)} points "
              "(several minutes) …", flush=True)
        coords = [WEATHER_POINTS[c][p] for c, p in keys]
        params = {
            "latitude": ",".join(f"{lat}" for lat, _ in coords),
            "longitude": ",".join(f"{lon}" for _, lon in coords),
            "hourly": ",".join(f"{v}_previous_day{d}" for d in (2, 3) for v in WEATHER_VARS),
            "timezone": "UTC", "start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}",
        }
        data = _get_open_meteo(params)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
        time.sleep(60)  # let the per-minute limit reset
    frames = {}
    for (country, point), d in zip(keys, data):
        h = dict(d["hourly"])
        idx = pd.to_datetime(h.pop("time"), utc=True)
        frames[f"{country}_{point}"] = pd.DataFrame(h, index=idx)
    return pd.concat(frames, axis=1)


def load_weather_d2(refresh: bool = False) -> pd.DataFrame:
    """Weather forecast for every hour, as it was available on day D (2 days before) at ~12:00.

    Columns `{country}_{point}_{variable}`, index UTC. For early hours (≤ DAY2_MAX_UTC_HOUR) the
    value comes from the run of 2 days before, otherwise from the run of 3 days before (the run of
    2 days before would not yet be published at 12:00).
    """
    halves = pd.date_range(START, pd.Timestamp.now(tz="UTC"), freq="6MS")
    raw = pd.concat([_fetch_weather_half(h, refresh) for h in halves]).sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]
    early = raw.index.hour <= DAY2_MAX_UTC_HOUR
    parts = []
    for country, points in WEATHER_POINTS.items():
        for point in points:
            for var in WEATHER_VARS:
                d2 = raw[(f"{country}_{point}", f"{var}_previous_day2")]
                d3 = raw[(f"{country}_{point}", f"{var}_previous_day3")]
                parts.append(pd.Series(np.where(early, d2, d3), index=raw.index,
                                       name=f"{country}_{point}_{var}"))
    return pd.concat(parts, axis=1).astype(float)


# --------------------------------------------------------------------------------------------
# Candidate predictors
# --------------------------------------------------------------------------------------------
# Group per predictor (for colours and readability of the matrix)
GROUPS: dict[str, list[str]] = {
    "Target": [TARGET, TARGET_DELTA],
    "CH price lags": ["CH lag 24h", "CH lag 48h", "CH lag 168h", "CH mean previous day", "CH mean 7 days"],
    "Neighbour prices (lag 24h)": ["DE lag 24h", "FR lag 24h", "IT-North lag 24h", "AT lag 24h"],
    "Neighbour prices same hour*": ["DE same hour*", "FR same hour*", "IT-North same hour*", "AT same hour*"],
    "Weather CH (D+2)": ["Temperature CH (D+2)", "Radiation CH (D+2)", "Wind 100m CH (D+2)",
                         "Precipitation 7 days CH"],
    "Weather abroad (D+2)": [f"{v} {c} (D+2)" for c in ("DE", "FR", "IT", "AT")
                             for v in ("Temperature", "Radiation", "Wind 100m")],
    "Hydro": ["Reservoir level %", "Reservoir level anomaly", "Run-of-river actual (lag 72h)",
              "Storage hydro actual (lag 72h)"],
    "Nuclear & trade": ["Nuclear CH actual (lag 72h)", "Nuclear FR actual (lag 72h)", "Net import CH actual (lag 72h)"],
    "Fuels": ["Gas price THE", "CO2 price EU", "Gas plant marginal cost"],
    "Calendar": ["Hour sin", "Hour cos", "Weekend", "Holiday CH", "Holiday DE", "Month sin", "Month cos"],
}
GROUP_OF = {c: g for g, cols in GROUPS.items() for c in cols}


def build_candidates(df: pd.DataFrame, storage: pd.DataFrame, fuel: pd.DataFrame,
                     weather: pd.DataFrame) -> pd.DataFrame:
    """All candidate predictors as an hourly table (index UTC), with the lags of the D+2 situation.

    `weather` = result of `load_weather_d2` (weather forecast as available on day D).
    """
    df = df.rename_axis("time_utc")
    X = pd.DataFrame(index=df.index)
    local = df.index.tz_convert(TZ)
    date = pd.Series(local.date, index=df.index)
    p = df["price_ch"]

    X[TARGET] = p
    X[TARGET_DELTA] = p - p.shift(24)

    # Price lags: for an hour in D+2, D+1 is fully known
    for lag in (24, 48, 168):
        X[f"CH lag {lag}h"] = p.shift(lag)
    daily = p.groupby(date.values).mean()
    X["CH mean previous day"] = (date - pd.Timedelta(days=1)).map(daily).to_numpy()
    X["CH mean 7 days"] = (date - pd.Timedelta(days=1)).map(daily.rolling(7).mean()).to_numpy()

    for key, label in (("de", "DE"), ("fr", "FR"), ("it", "IT-North"), ("at", "AT")):
        X[f"{label} lag 24h"] = df[f"price_{key}"].shift(24)
        X[f"{label} same hour*"] = df[f"price_{key}"]

    # Weather forecast for D+2: mean of the points per country
    wx = weather.reindex(df.index)
    names = {"temperature_2m": "Temperature", "shortwave_radiation": "Radiation", "wind_speed_100m": "Wind 100m"}
    for key, label in COUNTRIES.items():
        for var, name in names.items():
            X[f"{name} {label} (D+2)"] = wx[[f"{key}_{pt}_{var}" for pt in WEATHER_POINTS[key]]].mean(axis=1)
    rain = wx[[f"ch_{pt}_precipitation" for pt in WEATHER_POINTS["ch"]]].mean(axis=1)
    X["Precipitation 7 days CH"] = rain.rolling(168, min_periods=24).sum()

    # Reservoir level: value of at least 7 days ago (SFOE publishes with a delay)
    st = storage[["Reservoir level %", "Reservoir level anomaly"]].copy()
    st.index = st.index + pd.Timedelta(days=7)
    X = pd.merge_asof(X.reset_index(), st.reset_index(names="t").sort_values("t"),
                      left_on="time_utc", right_on="t", direction="backward").drop(columns="t").set_index("time_utc")

    # Actuals only with a 72 h lag
    X["Run-of-river actual (lag 72h)"] = df["act_ch_hydro_run_of_river"].shift(72)
    X["Storage hydro actual (lag 72h)"] = df["act_ch_hydro_water_reservoir"].shift(72)
    X["Nuclear CH actual (lag 72h)"] = df["act_ch_nuclear"].shift(72)
    X["Nuclear FR actual (lag 72h)"] = df["act_fr_nuclear"].shift(72)
    X["Net import CH actual (lag 72h)"] = df["cbet_ch_sum"].shift(72)

    # Gas/CO2: daily value of day D (2 days before the target day)
    for col in fuel:
        X[col] = (date - pd.Timedelta(days=2)).map(fuel[col]).to_numpy()

    # Calendar (local time)
    years = range(local.year.min(), local.year.max() + 1)
    ch_hol = holidays.country_holidays("CH", subdiv="ZH", years=years)
    de_hol = holidays.country_holidays("DE", years=years)
    X["Hour sin"], X["Hour cos"] = np.sin(2 * np.pi * local.hour / 24), np.cos(2 * np.pi * local.hour / 24)
    X["Weekend"] = (local.day_of_week >= 5).astype(int)
    X["Holiday CH"] = pd.Index(local.date).map(lambda d: d in ch_hol).astype(int)
    X["Holiday DE"] = pd.Index(local.date).map(lambda d: d in de_hol).astype(int)
    X["Month sin"], X["Month cos"] = np.sin(2 * np.pi * (local.month - 1) / 12), np.cos(2 * np.pi * (local.month - 1) / 12)

    order = [c for cols in GROUPS.values() for c in cols]
    return X[order]


def tso_features(df: pd.DataFrame) -> pd.DataFrame:
    """Solar, wind and residual load from the TSO forecast for the next day.

    Only for the comparison in the pretest: for a D+2 forecast this forecast would not yet be
    published (it appears in the evening of D+1).
    """
    def part(c: str, kinds: list[str]) -> pd.Series:
        cols = [f"fc_{c}_{k}" for k in kinds if f"fc_{c}_{k}" in df]
        return df[cols].sum(axis=1, min_count=len(cols))

    out = pd.DataFrame(index=df.index)
    for c, label in COUNTRIES.items():
        load, pv, wind = part(c, ["load"]), part(c, ["solar"]), part(c, ["wind_onshore", "wind_offshore"])
        out[f"Residual load {label} TSO"] = load - pv - wind
        out[f"Solar {label} TSO"], out[f"Wind {label} TSO"] = pv, wind
    return out


def fill_gaps(X: pd.DataFrame, max_gap_h: int = 168) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fill short gaps with the value of the same hour on the previous day, otherwise the previous week.

    Only gaps of up to `max_gap_h` consecutive hours are filled; longer blocks (e.g. the weeks
    before the Open-Meteo forecasts start) stay empty, because a previous-day value would only be
    invented there. Targets are never filled. Returns (filled table, report per column).
    """
    out = X.copy()
    rows = []
    for col in X:
        s = X[col]
        if GROUP_OF.get(col) == "Target" or not s.isna().any():
            continue
        na = s.isna()
        run = (na != na.shift()).cumsum()
        run_len = na.groupby(run).transform("sum")
        fillable = na & (run_len <= max_gap_h)
        filled = s.copy()
        for _ in range(max_gap_h // 24 + 1):  # previous day, repeated for longer gaps
            filled = filled.where(~(fillable & filled.isna()), filled.shift(24))
        by_day = int((fillable & filled.notna()).sum())
        filled = filled.where(~(fillable & filled.isna()), filled.shift(168))
        out[col] = filled
        rows.append({"Predictor": col, "missing": int(na.sum()), "previous day": by_day,
                     "previous week": int((fillable & filled.notna()).sum()) - by_day,
                     "still empty": int(filled.isna().sum())})
    return out, pd.DataFrame(rows).set_index("Predictor")


# --------------------------------------------------------------------------------------------
# Description and decision per predictor
# --------------------------------------------------------------------------------------------
_FOREIGN = {"DE": ("Germany", 9, " (incl. North Sea)"), "FR": ("France", 8, ""),
            "IT": ("Italy", 8, ""), "AT": ("Austria", 5, "")}

# Type, unit/range, meaning, available as
PREDICTOR_INFO: dict[str, tuple[str, str, str, str]] = {
    "CH lag 24h": ("continuous", "EUR/MWh", "Swiss price in the same hour on the previous day (D+1, already known)", "lag 24 h"),
    "CH lag 48h": ("continuous", "EUR/MWh", "Swiss price in the same hour two days earlier", "lag 48 h"),
    "CH lag 168h": ("continuous", "EUR/MWh", "Swiss price in the same hour one week earlier (same weekday)", "lag 168 h"),
    "CH mean previous day": ("continuous", "EUR/MWh", "daily mean of the Swiss price on the previous day", "day D+1"),
    "CH mean 7 days": ("continuous", "EUR/MWh", "mean of the last 7 daily means: current price level", "up to D+1"),
    "DE lag 24h": ("continuous", "EUR/MWh", "German/Luxembourg price, same hour, previous day", "lag 24 h"),
    "FR lag 24h": ("continuous", "EUR/MWh", "French price, same hour, previous day", "lag 24 h"),
    "IT-North lag 24h": ("continuous", "EUR/MWh", "North Italian price, same hour, previous day", "lag 24 h"),
    "AT lag 24h": ("continuous", "EUR/MWh", "Austrian price, same hour, previous day", "lag 24 h"),
    "DE same hour*": ("continuous", "EUR/MWh", "German price in the same hour (comparison only)", "not available"),
    "FR same hour*": ("continuous", "EUR/MWh", "French price in the same hour (comparison only)", "not available"),
    "IT-North same hour*": ("continuous", "EUR/MWh", "North Italian price in the same hour (comparison only)", "not available"),
    "AT same hour*": ("continuous", "EUR/MWh", "Austrian price in the same hour (comparison only)", "not available"),
    "Temperature CH (D+2)": ("continuous", "°C", "forecast air temperature for D+2, mean of 5 Swiss cities (drives consumption: heating, cooling)", "forecast of day D"),
    "Radiation CH (D+2)": ("continuous", "W/m²", "forecast solar radiation for D+2, mean of 5 Swiss cities (drives solar production)", "forecast of day D"),
    "Wind 100m CH (D+2)": ("continuous", "km/h", "forecast wind speed at 100 m (hub height), mean of 5 Swiss points", "forecast of day D"),
    "Precipitation 7 days CH": ("continuous", "mm", "precipitation of the last 7 days (inflow to rivers and lakes)", "forecast of day D"),
    **{k: v for c, (land, n, extra) in _FOREIGN.items() for k, v in {
        f"Temperature {c} (D+2)": ("continuous", "°C", f"forecast air temperature {land}, mean of {n} points", "forecast of day D"),
        f"Radiation {c} (D+2)": ("continuous", "W/m²", f"forecast solar radiation {land}, mean of {n} points", "forecast of day D"),
        f"Wind 100m {c} (D+2)": ("continuous", "km/h", f"forecast wind speed at 100 m {land}, mean of {n} points{extra}", "forecast of day D"),
    }.items()},
    "Reservoir level %": ("percent", "0–100 %", "filling level of the Swiss storage lakes in % of capacity (SFOE)", "lag 7 days"),
    "Reservoir level anomaly": ("percentage points", "± pp", "filling level minus the 2015–2023 mean of the same week: positive = more water than usual", "lag 7 days"),
    "Run-of-river actual (lag 72h)": ("continuous", "MW", "measured run-of-river production CH, 3 days earlier", "lag 72 h"),
    "Storage hydro actual (lag 72h)": ("continuous", "MW", "measured storage-hydro production CH, 3 days earlier", "lag 72 h"),
    "Nuclear CH actual (lag 72h)": ("continuous", "MW", "measured Swiss nuclear production, 3 days earlier; drops during outages and revisions (e.g. Gösgen 2025)", "lag 72 h"),
    "Nuclear FR actual (lag 72h)": ("continuous", "MW", "measured French nuclear production, 3 days earlier", "lag 72 h"),
    "Net import CH actual (lag 72h)": ("continuous", "GW", "Swiss imports minus exports, 3 days earlier (+ = import)", "lag 72 h"),
    "Gas price THE": ("continuous", "EUR/MWh", "natural gas price at the THE hub (daily)", "day D"),
    "CO2 price EU": ("continuous", "EUR/t", "price of an EU CO₂ allowance (auction, daily)", "day D"),
    "Gas plant marginal cost": ("continuous", "EUR/MWh", "2 × gas + 0.4 × CO₂: cost of one MWh from a gas plant", "day D"),
    "Hour sin": ("cyclic", "−1…1", "hour of the day on a circle (sine); 23:00 lies next to 00:00", "calendar"),
    "Hour cos": ("cyclic", "−1…1", "hour of the day on a circle (cosine); unique together with sine", "calendar"),
    "Weekend": ("binary", "0/1", "1 on Saturday and Sunday", "calendar"),
    "Holiday CH": ("binary", "0/1", "1 on public holidays in the canton of Zurich", "calendar"),
    "Holiday DE": ("binary", "0/1", "1 on nationwide German public holidays", "calendar"),
    "Month sin": ("cyclic", "−1…1", "month on a circle (sine); December lies next to January", "calendar"),
    "Month cos": ("cyclic", "−1…1", "month on a circle (cosine)", "calendar"),
}
# Same order as the columns of `build_candidates`
PREDICTOR_INFO = {c: PREDICTOR_INFO[c] for g, cols in GROUPS.items() if g != "Target" for c in cols}

KEEP = ["CH lag 24h", "CH lag 168h", "CH mean 7 days", "Temperature CH (D+2)", "Radiation CH (D+2)",
        "Wind 100m DE (D+2)", "Gas plant marginal cost", "Reservoir level anomaly", "Hour sin", "Hour cos",
        "Weekend", "Holiday CH", "Month sin", "Month cos"]
DROP = ["CH lag 48h", "AT lag 24h", "IT-North lag 24h", "CH mean previous day", "Gas price THE", "Holiday DE",
        *[f"{v} {c} (D+2)" for v in ("Temperature", "Radiation") for c in ("DE", "FR", "IT", "AT")],
        "Wind 100m CH (D+2)", "Wind 100m IT (D+2)", "Wind 100m AT (D+2)", "Storage hydro actual (lag 72h)",
        "Reservoir level %"]
NOT_AVAILABLE = ["DE same hour*", "FR same hour*", "IT-North same hour*", "AT same hour*"]
TEST = ["DE lag 24h", "FR lag 24h", "Wind 100m FR (D+2)", "CO2 price EU", "Nuclear CH actual (lag 72h)",
        "Nuclear FR actual (lag 72h)", "Run-of-river actual (lag 72h)", "Net import CH actual (lag 72h)",
        "Precipitation 7 days CH"]
DECISION = {**{c: "keep" for c in KEEP}, **{c: "drop" for c in DROP},
            **{c: "not available" for c in NOT_AVAILABLE}, **{c: "test in model" for c in TEST}}


def predictor_table() -> pd.DataFrame:
    """Overview of all candidates: group, type, unit, meaning, availability, decision."""
    rows = [{"Predictor": c, "Group": GROUP_OF[c], "Type": t, "Unit": u, "Meaning": e,
             "Available as": a, "Decision": DECISION[c]} for c, (t, u, e, a) in PREDICTOR_INFO.items()]
    return pd.DataFrame(rows).set_index("Predictor")


# --------------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------------
def redundant_pairs(corr: pd.DataFrame, threshold: float = 0.9) -> pd.DataFrame:
    """Predictor pairs with |ρ| ≥ threshold (without targets)."""
    feats = [c for c in corr if GROUP_OF[c] != "Target"]
    c = corr.loc[feats, feats]
    mask = np.triu(np.ones(c.shape, dtype=bool), k=1)
    pairs = c.where(mask).stack().rename("ρ").reset_index()
    pairs.columns = ["Predictor A", "Predictor B", "ρ"]
    return pairs[pairs["ρ"].abs() >= threshold].sort_values("ρ", key=np.abs, ascending=False).reset_index(drop=True)


def cluster_shortlist(corr: pd.DataFrame, relevance: pd.Series, threshold: float = 0.2) -> pd.DataFrame:
    """Hierarchical clustering with distance 1 − |ρ|; per cluster the most relevant predictor is suggested.

    threshold = 0.2 groups predictors with |ρ| ≥ 0.8 into one cluster.
    """
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    feats = [c for c in corr if GROUP_OF[c] != "Target"]
    dist = (1 - corr.loc[feats, feats].abs()).to_numpy(copy=True)
    np.fill_diagonal(dist, 0)
    dist = (dist + dist.T) / 2  # exactly symmetric for squareform
    labels = fcluster(linkage(squareform(dist, checks=False), "average"), threshold, criterion="distance")
    out = pd.DataFrame({"Cluster": labels, "Group": [GROUP_OF[f] for f in feats],
                        "Relevance": relevance.reindex(feats).to_numpy()}, index=feats)
    out["Representative"] = out.groupby("Cluster")["Relevance"].transform("max") == out["Relevance"]
    return out.sort_values(["Cluster", "Relevance"], ascending=[True, False])
