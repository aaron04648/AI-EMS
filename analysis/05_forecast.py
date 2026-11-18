"""Step 05 – rolling 24h forecast as JSON for the analysis dashboard of the website.

From the current point in time (quarter hour) the CH day-ahead price is simulated for the next 24 h.
The model (parameters from step 04) only knows actual prices up to now; inputs are the grid operator and
weather forecasts as well as already published day-ahead prices (lags ≥ 24 h). For the 7 days before,
there is, for comparison, the forecast of a model that only knows data before these 7 days.

Output: web/public/forecast/forecast.json
  hourly  – actual (up to now) and forecast, hourly, now − 7 days … now + 30 h
  reasons – SHAP contributions per driver and hour for the forecast hours

The CH day-ahead price is published hourly on energy-charts; the dashboard shows each hour as
four identical quarter hours. The buffer of 6 h beyond the 24 h keeps the dashboard current for
a few more hours after a run.

Usage:   .venv/bin/python 01_fetch_data.py   (current data)
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
HORIZON = pd.Timedelta(hours=30)  # 24 h + buffer
BASE = "lag_ch_24"

# Short names for the dashboard
LABELS = {
    "Load/residual load forecast": "Residual load",
    "Price level (lags CH)": "Price level",
    "Neighbouring prices (lags)": "Neighbouring countries",
    "Solar forecast": "Solar",
    "Wind forecast": "Wind",
    "Hydro & exchange (lags)": "Hydro & exchange",
    "Calendar": "Calendar",
    "Other weather": "Weather",
    "Other": "Other",
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
    hour = now.floor("h")  # the current partial hour belongs to the forecast
    end = hour + HORIZON

    # Create rows up to the end of the horizon, even where no measurements exist yet
    df = processed_hourly()
    df = df.reindex(pd.date_range(df.index.min(), max(df.index.max(), end), freq="1h"))
    X = build(df)
    cols = feature_columns(X)

    future = X[(X.index >= hour) & (X.index < end)]
    if future[BASE].notna().sum() < 20:
        raise SystemExit("Too little input data for the next 24 h – run 01_fetch_data.py first.")

    past = X[(X.index >= hour - HISTORY) & (X.index < hour)]
    m_past = fit_before(X, cols, params, hour - HISTORY)
    m_now = fit_before(X, cols, params, hour)
    pred = pd.concat([
        pd.Series(m_past.predict(past[cols]) + past[BASE], index=past.index),
        pd.Series(m_now.predict(future[cols]) + future[BASE], index=future.index),
    ])
    actual = X["y"].reindex(pred.index).where(pred.index < hour)

    # Reasons per forecast hour (SHAP, grouped into drivers)
    sv = pd.DataFrame(shap.TreeExplainer(m_now).shap_values(future[cols]), index=future.index, columns=cols)
    grouped = sv.T.groupby(lambda f: LABELS.get(driver_group(f), "Other")).sum().T
    top = [g for g in grouped.abs().sum().sort_values(ascending=False).index if g != "Other"][:6]
    rest = grouped.drop(columns=top).sum(axis=1)

    metrics = pd.read_csv(OUT / "model" / "metrics.csv").set_index("Model")
    hist = pd.concat([actual, pred], axis=1).dropna()
    recent = float(100 * (hist.iloc[:, 0] - hist.iloc[:, 1]).abs().mean() / hist.iloc[:, 0].abs().mean())

    out = {
        "created": pd.Timestamp.now(tz=TZ).isoformat(timespec="minutes"),
        "now": int(now.timestamp()),
        "accuracy": {
            "nmae": round(float(metrics.loc["LightGBM", "nMAE_%"]), 2),
            "persistenceNmae": round(float(metrics.loc["Persistence 24 h", "nMAE_%"]), 2),
            "recentNmae": round(recent, 2),
        },
        "hourly": {"t": unix(pred.index), "actual": nums(actual), "forecast": nums(pred)},
        "reasons": {
            "t": unix(future.index),
            "groups": top + ["Other"],
            "contrib": {**{g: nums(grouped[g]) for g in top}, "Other": nums(rest)},
        },
    }
    TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    TARGET_FILE.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    window = pred[(pred.index >= hour) & (pred.index < hour + pd.Timedelta(hours=24))]
    print(f"→ {TARGET_FILE}  (from {now.tz_convert(TZ):%d.%m. %H:%M}, avg. 24 h {window.mean():.1f} EUR/MWh)")


if __name__ == "__main__":
    main()
