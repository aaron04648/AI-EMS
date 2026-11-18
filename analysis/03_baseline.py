"""Step 03 – feature engineering & baselines: the benchmark for the MAE.

Builds the feature matrix (features.py), stores it under data/processed/features.csv and
evaluates simple reference models on the holdout (from TEST_START):
  * Persistence 24 h   – price tomorrow = price today at the same hour
  * Persistence 168 h  – price tomorrow = price one week ago at the same hour
  * Profile 14 days    – mean of the same hour over the last 14 days, separately for weekday/weekend
                         (corresponds to the current fallback forecast in the calculator, api/app/models/tariff_forecast.py)
  * Ridge regression   – linear model on all features (retrained monthly)

Usage:  .venv/bin/python 03_baseline.py
"""

from __future__ import annotations

import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from common import PROCESSED, TEST_START, TZ, metrics, out_dir, processed_hourly, profile_forecast
from features import build, feature_columns

OUT = out_dir("baseline")


def walk_forward_ridge(X: pd.DataFrame) -> pd.Series:
    cols = feature_columns(X)
    preds = []
    months = pd.date_range(TEST_START.tz_convert(TZ), X.index.max().tz_convert(TZ), freq="MS")
    months = [TEST_START] + [m.tz_convert("UTC") for m in months if m.tz_convert("UTC") > TEST_START]
    for i, start in enumerate(months):
        end = months[i + 1] if i + 1 < len(months) else X.index.max() + pd.Timedelta(hours=1)
        train = X[(X.index < start) & X["y"].notna()]
        test = X[(X.index >= start) & (X.index < end)]
        model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=10.0))
        model.fit(train[cols], train["y"])
        preds.append(pd.Series(model.predict(test[cols]), index=test.index))
    return pd.concat(preds)


def main() -> None:
    df = processed_hourly()
    X = build(df)
    X.to_csv(PROCESSED / "features.csv")
    print(f"Feature matrix: {X.shape[0]} hours × {len(feature_columns(X))} features")

    y = X["y"]
    test = y[(y.index >= TEST_START) & y.notna()]
    forecasts = {
        "Persistence 24 h": X["lag_ch_24"],
        "Persistence 168 h": X["lag_ch_168"],
        "Profile 14 days": profile_forecast(y),
        "Ridge regression": walk_forward_ridge(X),
    }
    rows = []
    for name, pred in forecasts.items():
        rows.append({"Model": name, **metrics(test, pred.reindex(test.index))})
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "metrics.csv", index=False, float_format="%.3f")
    pd.DataFrame({"actual": test, **{k: v.reindex(test.index) for k, v in forecasts.items()}}) \
        .to_csv(OUT / "predictions.csv")

    text = (f"# Baselines (Holdout {test.index.min():%Y-%m-%d} – {test.index.max():%Y-%m-%d}, {len(test)} hours)\n\n"
            f"Mean absolute price in the holdout: {test.abs().mean():.1f} EUR/MWh\n\n"
            + table.to_markdown(index=False, floatfmt=".2f") + "\n")
    (OUT / "summary.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
