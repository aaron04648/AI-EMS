"""Datenaufbereitung für das 48h-Profil (24 h bekannt + 24 h prognostiziert).

Prognosesituation (D+2):
    Am Tag D, nach Veröffentlichung der CH-Auktion für D+1 (~12 Uhr), wird jede Stunde t von D+2
    prognostiziert. Bekannt sind dann:
      * alle CH- und Nachbarpreise bis Ende D+1            → Preis-Lags ≥ 24 h
      * Ist-Erzeugung und Handel (ENTSO-E) bis etwa D früh → Ist-Werte nur mit Lag ≥ 72 h
      * Gas- und CO2-Preis von Tag D                        → Tageswert mit 2 Tagen Versatz
      * Speicherfüllstand (BFE, wöchentlich, verzögert)     → Stand von vor ≥ 7 Tagen
      * Wetterprognosen für D+2 (Open-Meteo, Lauf von Tag D)  → direkt als Prädiktoren

Die Prognosen der Netzbetreiber (PV, Wind, Last) erscheinen erst am Abend von D+1 und sind zum
Prognosezeitpunkt nicht bekannt. Stattdessen gehen die Wetterprognosen, die am Tag D schon
vorlagen, direkt ins Preismodell.

Quellen:
  * analysis/01_fetch_data.py: Preise, Ist-Erzeugung, Netzbetreiber-Prognosen (energy-charts API,
    stündlich ab 2024). Fehlt die Datei, startet `load_hourly` den Download (~30 min).
  * Open-Meteo Previous Runs API: Wetterprognosen, wie sie zwei bzw. drei Tage vorher vorlagen.
  * energy-charts.info Chart-Daten (nicht in der API): Speicherfüllstand CH, Gas-/CO2-Preise.
Alle Downloads werden unter data/raw/ zwischengespeichert.
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

TARGET = "Preis CH"
TARGET_DELTA = "Δ Preis CH zu Vortag"

COUNTRIES = {"ch": "CH", "de": "DE", "fr": "FR", "it": "IT", "at": "AT"}

# Wetterpunkte je Land: Städte (Last, PV) und Windregionen
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

# Bis zu dieser UTC-Stunde von D+2 ist der Lauf von zwei Tagen vorher am Tag D um ~12 Uhr schon
# verfügbar (Lauf um h UTC an Tag D, publiziert ~4 h später). Spätere Stunden nehmen den Lauf
# von drei Tagen vorher.
DAY2_MAX_UTC_HOUR = 6


# --------------------------------------------------------------------------------------------
# Laden
# --------------------------------------------------------------------------------------------
def load_hourly(path: Path = HOURLY_CSV) -> pd.DataFrame:
    """Stündlicher Datensatz aus analysis/01_fetch_data.py (Index UTC).

    Fehlt die Datei (z. B. frisch geklontes Repo), wird der Download gestartet (~30 min, die
    energy-charts-API drosselt). Danach liegt alles im Cache.
    """
    if not path.exists():
        script = ROOT / "analysis" / "01_fetch_data.py"
        venv = ROOT / "analysis" / ".venv" / "bin" / "python"
        python = str(venv) if venv.exists() else sys.executable
        print(f"{path.relative_to(ROOT)} fehlt – starte {script.relative_to(ROOT)} (~30 min) …")
        subprocess.run([python, script.name], cwd=script.parent, check=True)
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.loc[START:]


def _fetch_chart(chart: str, file: str, cache: Path) -> list[dict]:
    """Eine Chart-JSON-Datei von energy-charts.info laden (mit Cache und Backoff bei 429/5xx)."""
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
    n = series["name"]
    if isinstance(n, list):
        n = n[0]
    return n.get("de", n.get("en")) if isinstance(n, dict) else str(n)


def _frame(series: list[dict]) -> pd.DataFrame:
    index = pd.to_datetime(series[0]["xAxisValues"], unit="ms", utc=True)
    return pd.DataFrame({_name(s): s["data"] for s in series}, index=index).apply(pd.to_numeric, errors="coerce")


def storage_week(index: pd.DatetimeIndex) -> np.ndarray:
    """Woche des Jahres 1–52 aus dem Tag des Jahres.

    Nicht die ISO-Woche: Diese ordnet Ende Dezember oft schon Woche 1 des Folgejahres zu,
    wodurch ein Dezemberwert mit dem Januar-Mittel verglichen würde.
    """
    return np.minimum((index.tz_convert(TZ).day_of_year.to_numpy() - 1) // 7 + 1, 52)


def load_storage_ch(years=range(2015, 2027)) -> pd.DataFrame:
    """Wöchentlicher Füllstand der Schweizer Speicherseen (BFE via energy-charts), in TWh und %.

    `Füllstand Anomalie` = Füllstand in % minus Mittel derselben Kalenderwoche 2015–2023.
    Der Füllstand selbst ist stark saisonal; erst die Abweichung zeigt, ob mehr oder weniger
    Wasser als üblich gespeichert ist (Knappheit → höhere Preise im Winter).
    """
    parts = [_frame(_fetch_chart("filling_level", f"ch/year_storage_{y}.json", RAW / "storage_ch" / f"{y}.json"))
             for y in years]
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    regions = ["Wallis", "Graubünden", "Tessin", "Übrige Schweiz"]
    out = pd.DataFrame(index=df.index)
    out["Füllstand TWh"] = df[regions].sum(axis=1, min_count=len(regions))
    out["Füllstand %"] = 100 * out["Füllstand TWh"] / df["Ganze Schweiz max. Speicher"]
    week = storage_week(out.index)
    clim = out.loc[out.index.year <= 2023, "Füllstand %"].groupby(week[out.index.year <= 2023]).mean()
    out["Füllstand Anomalie"] = out["Füllstand %"] - clim.reindex(week).to_numpy()
    return out.dropna()


EC_API = "https://api.energy-charts.info"
WARMUP_START = pd.Timestamp("2023-12-01", tz="UTC")
DATASET_CSV = ROOT / "data" / "processed" / "candidates_d2.csv"


def _slug(name: str) -> str:
    return name.lower().replace(" / ", "_").replace("-", "_").replace(" ", "_")


def load_warmup_2023() -> pd.DataFrame:
    """Dezember 2023 für die Spalten, aus denen Lags berechnet werden (gleiche Namen wie `load_hourly`).

    Ohne diese Vorlaufdaten wären die ersten Stunden 2024 leer: Lag 168h braucht die Woche davor,
    die Ist-Werte mit Lag 72h die letzten drei Tage 2023. Quelle wie analysis/01_fetch_data.py.
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
            time.sleep(5)  # energy-charts drosselt
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
    """Tageswerte Gas (THE), CO2 (EU-Auktion) und Grenzkosten-Proxy eines Gaskraftwerks (2×Gas + 0.4×CO2)."""
    parts = [_frame(_fetch_chart("price_spot_market", f"de/year_{y}.json", RAW / "spot_market_de" / f"{y}.json"))
             for y in years]
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    cols = {"Gas (NCG, THE)": "Gaspreis THE", "CO2 Emissionszertifikate, Auktion EU": "CO2-Preis EU",
            "2 × Gas + 0,4 × CO2 (DE)": "Gaskraftwerk Grenzkosten"}
    daily = df[list(cols)].rename(columns=cols)
    return daily.groupby(daily.index.tz_convert(TZ).date).mean()


# --------------------------------------------------------------------------------------------
# Wetterprognosen für D+2 (Open-Meteo Previous Runs)
# --------------------------------------------------------------------------------------------
def _get_open_meteo(params: dict) -> list[dict]:
    """GET mit Wiederholung; der Dienst antwortet bei Last manchmal minutenlang nicht."""
    for attempt in range(6):
        try:
            r = requests.get(OPEN_METEO, params=params, timeout=600)
        except requests.RequestException as exc:
            print(f"    Open-Meteo: {exc.__class__.__name__}, neuer Versuch …")
            time.sleep(30 * (attempt + 1))
            continue
        if r.status_code == 200:
            time.sleep(5)  # Minutenlimit der Gratis-API schonen
            try:
                data = r.json()
            except ValueError:  # Antwort abgeschnitten
                print("    Open-Meteo: unvollständige Antwort, neuer Versuch …", flush=True)
                time.sleep(30 * (attempt + 1))
                continue
            return data if isinstance(data, list) else [data]
        if r.status_code in (429, 500, 502, 503, 504):
            print(f"    Open-Meteo: HTTP {r.status_code}, warte …")
            time.sleep(60 * (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError("Open-Meteo antwortet nicht – später erneut versuchen (Cache bleibt erhalten).")


def _fetch_weather_half(start: pd.Timestamp, refresh: bool) -> pd.DataFrame:
    """Prognosen von 2 und 3 Tagen vorher für alle Punkte und ein Halbjahr.

    Der Dienst braucht pro Anfrage mehrere Minuten, deshalb wenige grosse Anfragen. Ein Halbjahr
    für 35 Punkte zählt bei Open-Meteo als ~450 Abfragen und bleibt so unter dem Minutenlimit (600).
    Abgeschlossene Halbjahre werden nur einmal geladen, das laufende höchstens einmal pro Tag.
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
        print(f"Open-Meteo: lade Wetterprognosen {start:%Y-%m} bis {end:%Y-%m} für {len(keys)} Punkte "
              "(einige Minuten) …", flush=True)
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
        time.sleep(60)  # Minutenlimit zurücksetzen lassen
    frames = {}
    for (country, point), d in zip(keys, data):
        h = dict(d["hourly"])
        idx = pd.to_datetime(h.pop("time"), utc=True)
        frames[f"{country}_{point}"] = pd.DataFrame(h, index=idx)
    return pd.concat(frames, axis=1)


def load_weather_d2(refresh: bool = False) -> pd.DataFrame:
    """Wetterprognose für jede Stunde, so wie sie am Tag D (2 Tage vor dem Zieltag) um ~12 Uhr vorlag.

    Spalten `{land}_{punkt}_{variable}`, Index UTC. Für frühe Stunden (≤ DAY2_MAX_UTC_HOUR) stammt der
    Wert aus dem Lauf von 2 Tagen vorher, sonst aus dem Lauf von 3 Tagen vorher (der Lauf von 2 Tagen
    vorher wäre um 12 Uhr noch nicht publiziert).
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
# Kandidaten-Prädiktoren
# --------------------------------------------------------------------------------------------
# Gruppe je Prädiktor (für Farben und Lesbarkeit der Matrix)
GROUPS: dict[str, list[str]] = {
    "Ziel": [TARGET, TARGET_DELTA],
    "Preis CH (Lags)": ["CH Lag 24h", "CH Lag 48h", "CH Lag 168h", "CH Mittel Vortag", "CH Mittel 7 Tage"],
    "Nachbarpreise (Lag 24h)": ["DE Lag 24h", "FR Lag 24h", "IT-Nord Lag 24h", "AT Lag 24h"],
    "Nachbarpreise gleichzeitig*": ["DE gleichzeitig*", "FR gleichzeitig*", "IT-Nord gleichzeitig*", "AT gleichzeitig*"],
    "Wetter CH (D+2)": ["Temperatur CH (D+2)", "Einstrahlung CH (D+2)", "Wind 100m CH (D+2)",
                        "Niederschlag 7 Tage CH"],
    "Wetter Ausland (D+2)": [f"{v} {c} (D+2)" for c in ("DE", "FR", "IT", "AT")
                             for v in ("Temperatur", "Einstrahlung", "Wind 100m")],
    "Wasser": ["Füllstand %", "Füllstand Anomalie", "Laufwasser Ist (Lag 72h)", "Speicherwasser Ist (Lag 72h)"],
    "Kernkraft & Handel": ["Kernkraft CH Ist (Lag 72h)", "Kernkraft FR Ist (Lag 72h)", "Nettoimport CH Ist (Lag 72h)"],
    "Brennstoffe": ["Gaspreis THE", "CO2-Preis EU", "Gaskraftwerk Grenzkosten"],
    "Kalender": ["Stunde sin", "Stunde cos", "Wochenende", "Feiertag CH", "Feiertag DE", "Monat sin", "Monat cos"],
}
GROUP_OF = {c: g for g, cols in GROUPS.items() for c in cols}


def build_candidates(df: pd.DataFrame, storage: pd.DataFrame, fuel: pd.DataFrame,
                     weather: pd.DataFrame) -> pd.DataFrame:
    """Alle Kandidaten-Prädiktoren als stündliche Tabelle (Index UTC), mit den Lags der D+2-Situation.

    `weather` = Ergebnis von `load_weather_d2` (Wetterprognose, wie sie am Tag D vorlag).
    """
    df = df.rename_axis("time_utc")
    X = pd.DataFrame(index=df.index)
    local = df.index.tz_convert(TZ)
    date = pd.Series(local.date, index=df.index)
    p = df["price_ch"]

    X[TARGET] = p
    X[TARGET_DELTA] = p - p.shift(24)

    # Preis-Lags: für eine Stunde in D+2 ist D+1 vollständig bekannt
    for lag in (24, 48, 168):
        X[f"CH Lag {lag}h"] = p.shift(lag)
    daily = p.groupby(date.values).mean()
    X["CH Mittel Vortag"] = (date - pd.Timedelta(days=1)).map(daily).to_numpy()
    X["CH Mittel 7 Tage"] = (date - pd.Timedelta(days=1)).map(daily.rolling(7).mean()).to_numpy()

    for key, label in (("de", "DE"), ("fr", "FR"), ("it", "IT-Nord"), ("at", "AT")):
        X[f"{label} Lag 24h"] = df[f"price_{key}"].shift(24)
        X[f"{label} gleichzeitig*"] = df[f"price_{key}"]

    # Wetterprognose D+2: Mittel der Punkte je Land
    wx = weather.reindex(df.index)
    names = {"temperature_2m": "Temperatur", "shortwave_radiation": "Einstrahlung", "wind_speed_100m": "Wind 100m"}
    for key, label in COUNTRIES.items():
        for var, name in names.items():
            X[f"{name} {label} (D+2)"] = wx[[f"{key}_{p}_{var}" for p in WEATHER_POINTS[key]]].mean(axis=1)
    X["Niederschlag CH"] = wx[[f"ch_{p}_precipitation" for p in WEATHER_POINTS["ch"]]].mean(axis=1)
    X["Niederschlag 7 Tage CH"] = X.pop("Niederschlag CH").rolling(168, min_periods=24).sum()

    # Speicherfüllstand: Stand von vor mindestens 7 Tagen (BFE publiziert verzögert)
    st = storage[["Füllstand %", "Füllstand Anomalie"]].copy()
    st.index = st.index + pd.Timedelta(days=7)
    X = pd.merge_asof(X.reset_index(), st.reset_index(names="t").sort_values("t"),
                      left_on="time_utc", right_on="t", direction="backward").drop(columns="t").set_index("time_utc")

    # Ist-Werte nur mit Lag 72 h
    X["Laufwasser Ist (Lag 72h)"] = df["act_ch_hydro_run_of_river"].shift(72)
    X["Speicherwasser Ist (Lag 72h)"] = df["act_ch_hydro_water_reservoir"].shift(72)
    X["Kernkraft CH Ist (Lag 72h)"] = df["act_ch_nuclear"].shift(72)
    X["Kernkraft FR Ist (Lag 72h)"] = df["act_fr_nuclear"].shift(72)
    X["Nettoimport CH Ist (Lag 72h)"] = df["cbet_ch_sum"].shift(72)

    # Gas/CO2: Tageswert von Tag D (2 Tage vor dem Zieltag)
    for col in fuel:
        X[col] = (date - pd.Timedelta(days=2)).map(fuel[col]).to_numpy()

    # Kalender (Lokalzeit)
    years = range(local.year.min(), local.year.max() + 1)
    ch_hol = holidays.country_holidays("CH", subdiv="ZH", years=years)
    de_hol = holidays.country_holidays("DE", years=years)
    X["Stunde sin"], X["Stunde cos"] = np.sin(2 * np.pi * local.hour / 24), np.cos(2 * np.pi * local.hour / 24)
    X["Wochenende"] = (local.day_of_week >= 5).astype(int)
    X["Feiertag CH"] = pd.Index(local.date).map(lambda d: d in ch_hol).astype(int)
    X["Feiertag DE"] = pd.Index(local.date).map(lambda d: d in de_hol).astype(int)
    X["Monat sin"], X["Monat cos"] = np.sin(2 * np.pi * (local.month - 1) / 12), np.cos(2 * np.pi * (local.month - 1) / 12)

    order = [c for cols in GROUPS.values() for c in cols]
    return X[order]


def tso_features(df: pd.DataFrame) -> pd.DataFrame:
    """PV, Wind und Residuallast aus der Netzbetreiber-Prognose für den Folgetag.

    Nur für den Vergleich im Vorabtest: Für eine Prognose von D+2 wäre diese Prognose noch nicht
    publiziert (sie erscheint am Abend von D+1).
    """
    def part(c: str, kinds: list[str]) -> pd.Series:
        cols = [f"fc_{c}_{k}" for k in kinds if f"fc_{c}_{k}" in df]
        return df[cols].sum(axis=1, min_count=len(cols))

    out = pd.DataFrame(index=df.index)
    for c, label in COUNTRIES.items():
        load, pv, wind = part(c, ["load"]), part(c, ["solar"]), part(c, ["wind_onshore", "wind_offshore"])
        out[f"Residuallast {label} TSO"] = load - pv - wind
        out[f"PV {label} TSO"], out[f"Wind {label} TSO"] = pv, wind
    return out


def fill_gaps(X: pd.DataFrame, max_gap_h: int = 168) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Füllt kurze Lücken mit dem Wert derselben Stunde am Vortag, sonst der Vorwoche.

    Nur Lücken bis `max_gap_h` Stunden am Stück werden gefüllt; längere Blöcke (z. B. die Wochen
    vor dem Start der Open-Meteo-Prognosen) bleiben leer, weil ein Vortageswert dort nur erfunden
    wäre. Zielgrössen werden nie gefüllt. Rückgabe: (gefüllte Tabelle, Bericht je Spalte).
    """
    out = X.copy()
    rows = []
    for col in X:
        s = X[col]
        if GROUP_OF.get(col) == "Ziel" or not s.isna().any():
            continue
        na = s.isna()
        run = (na != na.shift()).cumsum()
        run_len = na.groupby(run).transform("sum")
        fillable = na & (run_len <= max_gap_h)
        filled = s.copy()
        for _ in range(max_gap_h // 24 + 1):  # Vortag, bei längeren Lücken wiederholt
            filled = filled.where(~(fillable & filled.isna()), filled.shift(24))
        by_day = int((fillable & filled.notna()).sum())
        filled = filled.where(~(fillable & filled.isna()), filled.shift(168))
        out[col] = filled
        rows.append({"Prädiktor": col, "fehlend": int(na.sum()), "Vortag": by_day,
                     "Vorwoche": int((fillable & filled.notna()).sum()) - by_day,
                     "bleibt leer": int(filled.isna().sum())})
    return out, pd.DataFrame(rows).set_index("Prädiktor")


# --------------------------------------------------------------------------------------------
# Beschreibung und Entscheid je Prädiktor
# --------------------------------------------------------------------------------------------
# Typ, Einheit/Wertebereich, Erklärung, verfügbar als
PREDICTOR_INFO: dict[str, tuple[str, str, str, str]] = {
    "CH Lag 24h": ("stetig", "EUR/MWh", "CH-Preis derselben Stunde am Vortag (D+1, schon bekannt)", "Lag 24 h"),
    "CH Lag 48h": ("stetig", "EUR/MWh", "CH-Preis derselben Stunde zwei Tage vorher", "Lag 48 h"),
    "CH Lag 168h": ("stetig", "EUR/MWh", "CH-Preis derselben Stunde eine Woche vorher (gleicher Wochentag)", "Lag 168 h"),
    "CH Mittel Vortag": ("stetig", "EUR/MWh", "Tagesmittel des CH-Preises am Vortag", "Tag D+1"),
    "CH Mittel 7 Tage": ("stetig", "EUR/MWh", "Mittel der Tagesmittel der letzten 7 Tage: aktuelles Preisniveau", "bis D+1"),
    "DE Lag 24h": ("stetig", "EUR/MWh", "Preis Deutschland/Luxemburg derselben Stunde am Vortag", "Lag 24 h"),
    "FR Lag 24h": ("stetig", "EUR/MWh", "Preis Frankreich derselben Stunde am Vortag", "Lag 24 h"),
    "IT-Nord Lag 24h": ("stetig", "EUR/MWh", "Preis Norditalien derselben Stunde am Vortag", "Lag 24 h"),
    "AT Lag 24h": ("stetig", "EUR/MWh", "Preis Österreich derselben Stunde am Vortag", "Lag 24 h"),
    "DE gleichzeitig*": ("stetig", "EUR/MWh", "Preis Deutschland derselben Stunde (nur Vergleich)", "nicht verfügbar"),
    "FR gleichzeitig*": ("stetig", "EUR/MWh", "Preis Frankreich derselben Stunde (nur Vergleich)", "nicht verfügbar"),
    "IT-Nord gleichzeitig*": ("stetig", "EUR/MWh", "Preis Norditalien derselben Stunde (nur Vergleich)", "nicht verfügbar"),
    "AT gleichzeitig*": ("stetig", "EUR/MWh", "Preis Österreich derselben Stunde (nur Vergleich)", "nicht verfügbar"),
    "Temperatur CH (D+2)": ("stetig", "°C", "prognostizierte Lufttemperatur für übermorgen, Mittel von 5 CH-Städten (treibt den Verbrauch: Heizen, Kühlen)", "Prognose Tag D"),
    "Einstrahlung CH (D+2)": ("stetig", "W/m²", "prognostizierte Sonneneinstrahlung für übermorgen, Mittel von 5 CH-Städten (treibt die PV-Produktion)", "Prognose Tag D"),
    "Wind 100m CH (D+2)": ("stetig", "km/h", "prognostizierte Windgeschwindigkeit auf 100 m Höhe (Nabenhöhe), Mittel von 5 CH-Punkten", "Prognose Tag D"),
    "Temperatur DE (D+2)": ("stetig", "°C", "prognostizierte Lufttemperatur Deutschland, Mittel von 9 Punkten", "Prognose Tag D"),
    "Einstrahlung DE (D+2)": ("stetig", "W/m²", "prognostizierte Sonneneinstrahlung Deutschland, Mittel von 9 Punkten", "Prognose Tag D"),
    "Wind 100m DE (D+2)": ("stetig", "km/h", "prognostizierte Windgeschwindigkeit auf 100 m Deutschland, Mittel von 9 Punkten (inkl. Nordsee)", "Prognose Tag D"),
    "Temperatur FR (D+2)": ("stetig", "°C", "prognostizierte Lufttemperatur Frankreich, Mittel von 8 Punkten", "Prognose Tag D"),
    "Einstrahlung FR (D+2)": ("stetig", "W/m²", "prognostizierte Sonneneinstrahlung Frankreich, Mittel von 8 Punkten", "Prognose Tag D"),
    "Wind 100m FR (D+2)": ("stetig", "km/h", "prognostizierte Windgeschwindigkeit auf 100 m Frankreich, Mittel von 8 Punkten", "Prognose Tag D"),
    "Temperatur IT (D+2)": ("stetig", "°C", "prognostizierte Lufttemperatur Italien, Mittel von 8 Punkten", "Prognose Tag D"),
    "Einstrahlung IT (D+2)": ("stetig", "W/m²", "prognostizierte Sonneneinstrahlung Italien, Mittel von 8 Punkten", "Prognose Tag D"),
    "Wind 100m IT (D+2)": ("stetig", "km/h", "prognostizierte Windgeschwindigkeit auf 100 m Italien, Mittel von 8 Punkten", "Prognose Tag D"),
    "Temperatur AT (D+2)": ("stetig", "°C", "prognostizierte Lufttemperatur Österreich, Mittel von 5 Punkten", "Prognose Tag D"),
    "Einstrahlung AT (D+2)": ("stetig", "W/m²", "prognostizierte Sonneneinstrahlung Österreich, Mittel von 5 Punkten", "Prognose Tag D"),
    "Wind 100m AT (D+2)": ("stetig", "km/h", "prognostizierte Windgeschwindigkeit auf 100 m Österreich, Mittel von 5 Punkten", "Prognose Tag D"),
    "Niederschlag 7 Tage CH": ("stetig", "mm", "Niederschlagssumme der letzten 7 Tage (Zufluss zu Flüssen und Seen)", "Prognose Tag D"),
    "Füllstand %": ("Prozent", "0–100 %", "Füllstand der Schweizer Speicherseen in % der Kapazität (BFE)", "Lag 7 Tage"),
    "Füllstand Anomalie": ("Prozentpunkte", "± pp", "Füllstand minus Mittel derselben Woche 2015–2023: positiv = mehr Wasser als üblich", "Lag 7 Tage"),
    "Laufwasser Ist (Lag 72h)": ("stetig", "MW", "gemessene Produktion der Flusskraftwerke CH vor 3 Tagen", "Lag 72 h"),
    "Speicherwasser Ist (Lag 72h)": ("stetig", "MW", "gemessene Produktion der Speicherkraftwerke CH vor 3 Tagen", "Lag 72 h"),
    "Kernkraft CH Ist (Lag 72h)": ("stetig", "MW", "gemessene Kernkraftproduktion CH vor 3 Tagen; sinkt bei Ausfällen/Revisionen (z. B. Gösgen 2025)", "Lag 72 h"),
    "Kernkraft FR Ist (Lag 72h)": ("stetig", "MW", "gemessene Kernkraftproduktion Frankreich vor 3 Tagen", "Lag 72 h"),
    "Nettoimport CH Ist (Lag 72h)": ("stetig", "GW", "Import minus Export der Schweiz vor 3 Tagen (+ = Import)", "Lag 72 h"),
    "Gaspreis THE": ("stetig", "EUR/MWh", "Erdgaspreis am Handelspunkt THE (Tageswert)", "Tag D"),
    "CO2-Preis EU": ("stetig", "EUR/t", "Preis eines CO2-Zertifikats (EU-Auktion, Tageswert)", "Tag D"),
    "Gaskraftwerk Grenzkosten": ("stetig", "EUR/MWh", "2 × Gaspreis + 0.4 × CO2-Preis: Kosten einer MWh aus einem Gaskraftwerk", "Tag D"),
    "Stunde sin": ("zyklisch", "−1…1", "Tageszeit als Punkt auf einem Kreis (Sinus); 23 Uhr liegt so neben 0 Uhr", "Kalender"),
    "Stunde cos": ("zyklisch", "−1…1", "Tageszeit als Punkt auf einem Kreis (Kosinus); zusammen mit Sinus eindeutig", "Kalender"),
    "Wochenende": ("binär", "0/1", "1 an Samstag und Sonntag", "Kalender"),
    "Feiertag CH": ("binär", "0/1", "1 an Feiertagen im Kanton Zürich", "Kalender"),
    "Feiertag DE": ("binär", "0/1", "1 an bundesweiten Feiertagen in Deutschland", "Kalender"),
    "Monat sin": ("zyklisch", "−1…1", "Monat als Punkt auf einem Kreis (Sinus); Dezember liegt so neben Januar", "Kalender"),
    "Monat cos": ("zyklisch", "−1…1", "Monat als Punkt auf einem Kreis (Kosinus)", "Kalender"),
}

KEEP = ["CH Lag 24h", "CH Lag 168h", "CH Mittel 7 Tage", "Temperatur CH (D+2)", "Einstrahlung CH (D+2)",
        "Wind 100m DE (D+2)", "Gaskraftwerk Grenzkosten", "Füllstand Anomalie", "Stunde sin", "Stunde cos",
        "Wochenende", "Feiertag CH", "Monat sin", "Monat cos"]
DROP = ["CH Lag 48h", "AT Lag 24h", "IT-Nord Lag 24h", "CH Mittel Vortag", "Gaspreis THE", "Feiertag DE",
        *[f"{v} {c} (D+2)" for v in ("Temperatur", "Einstrahlung") for c in ("DE", "FR", "IT", "AT")],
        "Wind 100m CH (D+2)", "Wind 100m IT (D+2)", "Wind 100m AT (D+2)", "Speicherwasser Ist (Lag 72h)",
        "Füllstand %"]
NOT_AVAILABLE = ["DE gleichzeitig*", "FR gleichzeitig*", "IT-Nord gleichzeitig*", "AT gleichzeitig*"]
TEST = ["DE Lag 24h", "FR Lag 24h", "Wind 100m FR (D+2)", "CO2-Preis EU", "Kernkraft CH Ist (Lag 72h)",
        "Kernkraft FR Ist (Lag 72h)", "Laufwasser Ist (Lag 72h)", "Nettoimport CH Ist (Lag 72h)",
        "Niederschlag 7 Tage CH"]
DECISION = {**{c: "behalten" for c in KEEP}, **{c: "weglassen" for c in DROP},
            **{c: "nicht verfügbar" for c in NOT_AVAILABLE}, **{c: "im Modell testen" for c in TEST}}


def predictor_table() -> pd.DataFrame:
    """Übersicht aller Kandidaten: Gruppe, Typ, Einheit, Erklärung, Verfügbarkeit, Entscheid."""
    rows = [{"Prädiktor": c, "Gruppe": GROUP_OF[c], "Typ": t, "Einheit": u, "Erklärung": e,
             "verfügbar als": a, "Entscheid": DECISION[c]} for c, (t, u, e, a) in PREDICTOR_INFO.items()]
    return pd.DataFrame(rows).set_index("Prädiktor")


# --------------------------------------------------------------------------------------------
# Auswahl
# --------------------------------------------------------------------------------------------
def redundant_pairs(corr: pd.DataFrame, threshold: float = 0.9) -> pd.DataFrame:
    """Prädiktor-Paare mit |ρ| ≥ threshold (ohne Zielgrössen)."""
    feats = [c for c in corr if GROUP_OF[c] != "Ziel"]
    c = corr.loc[feats, feats]
    mask = np.triu(np.ones(c.shape, dtype=bool), k=1)
    pairs = c.where(mask).stack().rename("ρ").reset_index()
    pairs.columns = ["Prädiktor A", "Prädiktor B", "ρ"]
    return pairs[pairs["ρ"].abs() >= threshold].sort_values("ρ", key=np.abs, ascending=False).reset_index(drop=True)


def cluster_shortlist(corr: pd.DataFrame, relevance: pd.Series, threshold: float = 0.2) -> pd.DataFrame:
    """Hierarchisches Clustering mit Distanz 1 − |ρ|; je Cluster wird der relevanteste Prädiktor vorgeschlagen.

    threshold = 0.2 fasst Prädiktoren mit |ρ| ≥ 0.8 zu einem Cluster zusammen.
    """
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    feats = [c for c in corr if GROUP_OF[c] != "Ziel"]
    dist = (1 - corr.loc[feats, feats].abs()).to_numpy(copy=True)
    np.fill_diagonal(dist, 0)
    dist = (dist + dist.T) / 2  # numerisch exakt symmetrisch für squareform
    labels = fcluster(linkage(squareform(dist, checks=False), "average"), threshold, criterion="distance")
    out = pd.DataFrame({"Cluster": labels, "Gruppe": [GROUP_OF[f] for f in feats],
                        "Relevanz": relevance.reindex(feats).to_numpy()}, index=feats)
    out["Vertreter"] = out.groupby("Cluster")["Relevanz"].transform("max") == out["Relevanz"]
    return out.sort_values(["Cluster", "Relevanz"], ascending=[True, False])
