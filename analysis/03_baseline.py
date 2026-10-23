"""Schritt 03 – Feature Engineering & Baselines: die Messlatte für den MAE.

Baut die Feature-Matrix (features.py), speichert sie unter data/processed/features.csv und
bewertet einfache Referenzmodelle auf dem Holdout (ab TEST_START):
  * Persistenz 24 h   – Preis morgen = Preis heute zur selben Stunde
  * Persistenz 168 h  – Preis morgen = Preis vor einer Woche zur selben Stunde
  * Profil 14 Tage    – Mittel derselben Stunde der letzten 14 Tage, getrennt Werktag/Wochenende
                        (entspricht der heutigen Fallback-Prognose im Rechner, api/app/models/tariff_forecast.py)
  * Ridge-Regression  – lineares Modell auf allen Features (monatlich neu trainiert)

Aufruf:  .venv/bin/python 03_baseline.py
"""

from __future__ import annotations

import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from common import PROCESSED, TEST_START, TZ, metrics, out_dir, processed_hourly
from features import build, feature_columns

OUT = out_dir("baseline")


def profile_forecast(y: pd.Series, days: int = 14) -> pd.Series:
    """Für jeden Tag D: Mittel der letzten `days` Tage bis D-1, je Stunde und Tagtyp."""
    local = y.index.tz_convert(TZ)
    frame = pd.DataFrame({"y": y.values, "date": local.normalize(), "hour": local.hour,
                          "weekend": local.day_of_week >= 5}, index=y.index)
    table = frame.pivot_table(index="date", columns=["weekend", "hour"], values="y")
    # Je Tagtyp: Tage des anderen Typs sind NaN und fallen aus dem Mittel; shift(1) → nur Vergangenheit
    roll = table.rolling(days, min_periods=2).mean().shift(1).stack(level=[0, 1], future_stack=True)
    key = pd.MultiIndex.from_arrays([frame["date"], frame["weekend"], frame["hour"]])
    return pd.Series(roll.reindex(key).to_numpy(), index=y.index)


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
    print(f"Feature-Matrix: {X.shape[0]} Stunden × {len(feature_columns(X))} Features")

    y = X["y"]
    test = y[(y.index >= TEST_START) & y.notna()]
    forecasts = {
        "Persistenz 24 h": X["lag_ch_24"],
        "Persistenz 168 h": X["lag_ch_168"],
        "Profil 14 Tage": profile_forecast(y),
        "Ridge-Regression": walk_forward_ridge(X),
    }
    rows = []
    for name, pred in forecasts.items():
        rows.append({"Modell": name, **metrics(test, pred.reindex(test.index))})
    table = pd.DataFrame(rows)
    table.to_csv(OUT / "metrics.csv", index=False, float_format="%.3f")
    pd.DataFrame({"actual": test, **{k: v.reindex(test.index) for k, v in forecasts.items()}}) \
        .to_csv(OUT / "predictions.csv")

    text = (f"# Baselines (Holdout {test.index.min():%Y-%m-%d} – {test.index.max():%Y-%m-%d}, {len(test)} Stunden)\n\n"
            f"Mittlerer Absolutpreis im Holdout: {test.abs().mean():.1f} EUR/MWh\n\n"
            + table.to_markdown(index=False, floatfmt=".2f") + "\n")
    (OUT / "summary.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
