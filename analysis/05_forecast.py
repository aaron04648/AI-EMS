"""Schritt 05 – Rollende 24h-Prognose als JSON für das Analyse-Dashboard der Website.

Ab dem aktuellen Zeitpunkt (Viertelstunde) wird der CH-Day-Ahead-Preis für die nächsten 24 h simuliert.
Das Modell (Parameter aus Schritt 04) kennt nur Ist-Preise bis jetzt; Eingänge sind die Netzbetreiber- und
Wetterprognosen sowie bereits publizierte Day-Ahead-Preise (Lags ≥ 24 h). Für die 7 Tage davor gibt es
zum Vergleich die Prognose eines Modells, das nur Daten vor diesen 7 Tagen kennt.

Ausgabe: web/public/forecast/forecast.json
  hourly  – Ist (bis jetzt) und Prognose, stündlich, jetzt − 7 Tage … jetzt + 30 h
  reasons – SHAP-Beiträge je Treiber und Stunde für die Prognosestunden

Der CH-Day-Ahead-Preis wird auf energy-charts stündlich publiziert; das Dashboard zeigt jede Stunde als
vier gleiche Viertelstunden. Der Puffer von 6 h über die 24 h hinaus hält das Dashboard nach einem Lauf
noch einige Stunden aktuell.

Aufruf:  .venv/bin/python 01_fetch_data.py   (aktuelle Daten)
         .venv/bin/python 05_forecast.py [--now "YYYY-MM-DD HH:MM"]
"""

from __future__ import annotations

import json
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
import shap

from common import OUT, ROOT, TZ, processed_hourly
from features import build, driver_group, feature_columns

TARGET_FILE = ROOT.parent / "web" / "public" / "forecast" / "forecast.json"
HISTORY = pd.Timedelta(days=7)
HORIZON = pd.Timedelta(hours=30)  # 24 h + Puffer
BASE = "lag_ch_24"

# Kurze Namen für das Dashboard
LABELS = {
    "Last/Residuallast-Prognose": "Residuallast",
    "Preisniveau (Lags CH)": "Preisniveau",
    "Nachbarpreise (Lags)": "Nachbarländer",
    "Solar-Prognose": "Solar",
    "Wind-Prognose": "Wind",
    "Wasserkraft & Austausch (Lags)": "Wasser & Austausch",
    "Kalender": "Kalender",
    "Wetter sonstiges": "Wetter",
    "Sonstiges": "Übrige",
}


def fit_before(X: pd.DataFrame, cols: list[str], params: dict, cutoff: pd.Timestamp) -> lgb.LGBMRegressor:
    train = X[(X.index < cutoff) & X["y"].notna() & X[BASE].notna()]
    return lgb.LGBMRegressor(**params, random_state=42, verbose=-1, n_jobs=-1).fit(
        train[cols], train["y"] - train[BASE])


def nums(values) -> list[float | None]:
    return [None if v is None or not np.isfinite(v) else round(float(v), 2) for v in values]


def unix(index: pd.DatetimeIndex) -> list[int]:
    return [int(t.timestamp()) for t in index]


def main() -> None:
    params = json.loads((OUT / "model" / "best_params.json").read_text())
    now = pd.Timestamp(sys.argv[sys.argv.index("--now") + 1], tz=TZ) if "--now" in sys.argv \
        else pd.Timestamp.now(tz=TZ)
    now = now.floor("15min").tz_convert("UTC")
    hour = now.floor("h")  # die angebrochene Stunde gehört zur Prognose
    end = hour + HORIZON

    # Zeilen bis zum Ende des Horizonts anlegen, auch wo noch keine Messwerte existieren
    df = processed_hourly()
    df = df.reindex(pd.date_range(df.index.min(), max(df.index.max(), end), freq="1h"))
    X = build(df)
    cols = feature_columns(X)

    future = X[(X.index >= hour) & (X.index < end)]
    if future[BASE].notna().sum() < 20:
        raise SystemExit("Zu wenige Eingangsdaten für die nächsten 24 h – zuerst 01_fetch_data.py ausführen.")

    past = X[(X.index >= hour - HISTORY) & (X.index < hour)]
    m_past = fit_before(X, cols, params, hour - HISTORY)
    m_now = fit_before(X, cols, params, hour)
    pred = pd.concat([
        pd.Series(m_past.predict(past[cols]) + past[BASE], index=past.index),
        pd.Series(m_now.predict(future[cols]) + future[BASE], index=future.index),
    ])
    actual = X["y"].reindex(pred.index).where(pred.index < hour)

    # Gründe je Prognosestunde (SHAP, zu Treibern gruppiert)
    sv = pd.DataFrame(shap.TreeExplainer(m_now).shap_values(future[cols]), index=future.index, columns=cols)
    grouped = sv.T.groupby(lambda f: LABELS.get(driver_group(f), "Übrige")).sum().T
    top = [g for g in grouped.abs().sum().sort_values(ascending=False).index if g != "Übrige"][:6]
    rest = grouped.drop(columns=top).sum(axis=1)

    metrics = pd.read_csv(OUT / "model" / "metrics.csv").set_index("Modell")
    hist = pd.concat([actual, pred], axis=1).dropna()
    recent = float(100 * (hist.iloc[:, 0] - hist.iloc[:, 1]).abs().mean() / hist.iloc[:, 0].abs().mean())

    out = {
        "created": pd.Timestamp.now(tz=TZ).isoformat(timespec="minutes"),
        "now": int(now.timestamp()),
        "accuracy": {
            "nmae": round(float(metrics.loc["LightGBM", "nMAE_%"]), 2),
            "persistenceNmae": round(float(metrics.loc["Persistenz 24 h", "nMAE_%"]), 2),
            "recentNmae": round(recent, 2),
        },
        "hourly": {"t": unix(pred.index), "actual": nums(actual), "forecast": nums(pred)},
        "reasons": {
            "t": unix(future.index),
            "groups": top + ["Übrige"],
            "contrib": {**{g: nums(grouped[g]) for g in top}, "Übrige": nums(rest)},
        },
    }
    TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    TARGET_FILE.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    window = pred[(pred.index >= hour) & (pred.index < hour + pd.Timedelta(hours=24))]
    print(f"→ {TARGET_FILE}  (ab {now.tz_convert(TZ):%d.%m. %H:%M}, Ø 24 h {window.mean():.1f} EUR/MWh)")


if __name__ == "__main__":
    main()
