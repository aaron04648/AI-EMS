"""Step 04 – LightGBM forecast, hyperparameter tuning and SHAP explanation.

The model target is the change vs. the previous day at the same hour (y − lag_ch_24); the
forecast is previous-day price + model. This way the tree model does not have to extrapolate the
(strongly fluctuating) price level – the level is contained in the known previous-day price.

1. Tuning: random search over LightGBM parameters with TimeSeriesSplit (expanding window,
   24 h gap between training and validation – never shuffle randomly) on the data before TEST_START.
2. Holdout: walk-forward – for each month from TEST_START the model is retrained on all data before it
   and the month is forecast (as in real operation).
3. Robustness: the same evaluation without grid operator forecasts (weather + lags only).
4. SHAP: contribution of each feature or driver to every single hourly forecast.

Usage:  .venv/bin/python 04_model.py [--trials 30]
"""

from __future__ import annotations

import json
import sys

import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
from sklearn.model_selection import TimeSeriesSplit

from common import COLORS, INK, INK_2, PROCESSED, TEST_START, TZ, metrics, out_dir, plot_style, processed_hourly
from features import build, driver_group, feature_columns

OUT = out_dir("model")
TRIALS = int(sys.argv[sys.argv.index("--trials") + 1]) if "--trials" in sys.argv else 30
SEED = 42
BASE = "lag_ch_24"  # price yesterday, same hour (known at forecast time)
lines: list[str] = []


def note(text: str = "") -> None:
    print(text)
    lines.append(text)


def save(fig, name: str) -> None:
    fig.savefig(OUT / name)
    plt.close(fig)
    note(f"\n![{name}]({name})\n")


# --- Tuning ------------------------------------------------------------------------------------

def sample_params(rng: np.random.Generator) -> dict:
    return {
        "objective": rng.choice(["regression_l1", "huber", "regression"]),
        "learning_rate": float(rng.choice([0.02, 0.03, 0.05, 0.08])),
        "n_estimators": int(rng.choice([400, 700, 1000, 1500])),
        "num_leaves": int(rng.choice([15, 31, 63, 127])),
        "min_child_samples": int(rng.choice([10, 20, 50, 100])),
        "feature_fraction": float(rng.choice([0.5, 0.7, 0.9])),
        "bagging_fraction": float(rng.choice([0.7, 0.8, 1.0])),
        "bagging_freq": 1,
        "lambda_l2": float(rng.choice([0.0, 1.0, 5.0])),
    }


def model(params: dict) -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(**params, random_state=SEED, verbose=-1, n_jobs=-1)


def tune(X: pd.DataFrame, cols: list[str]) -> tuple[dict, pd.DataFrame]:
    data = X[(X.index < TEST_START) & X["y"].notna() & X[BASE].notna()]
    cv = TimeSeriesSplit(n_splits=5, test_size=24 * 60, gap=24)
    rng = np.random.default_rng(SEED)
    results = []
    for trial in range(TRIALS):
        params = sample_params(rng)
        maes = []
        for train_idx, val_idx in cv.split(data):
            tr, va = data.iloc[train_idx], data.iloc[val_idx]
            m = model(params).fit(tr[cols], tr["y"] - tr[BASE])
            maes.append(np.abs(m.predict(va[cols]) + va[BASE] - va["y"]).mean())
        results.append({**params, "cv_mae": float(np.mean(maes)), "cv_mae_std": float(np.std(maes))})
        print(f"  Trial {trial + 1:2d}/{TRIALS}: CV-MAE {np.mean(maes):6.2f}  ({params['objective']}, "
              f"lr={params['learning_rate']}, leaves={params['num_leaves']}, n={params['n_estimators']})")
    table = pd.DataFrame(results).sort_values("cv_mae")
    best = {k: v for k, v in table.iloc[0].items() if k not in ("cv_mae", "cv_mae_std")}
    for k in ("n_estimators", "num_leaves", "min_child_samples", "bagging_freq"):
        best[k] = int(best[k])
    return best, table


# --- Walk-forward holdout ---------------------------------------------------------------------------

def month_starts(X: pd.DataFrame) -> list[pd.Timestamp]:
    local = pd.date_range(TEST_START.tz_convert(TZ), X.index.max().tz_convert(TZ), freq="MS")
    return [TEST_START] + [m.tz_convert("UTC") for m in local if m.tz_convert("UTC") > TEST_START]


def walk_forward(X: pd.DataFrame, cols: list[str], params: dict, explain: bool = False):
    starts = month_starts(X)
    preds, shaps, last = [], [], None
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else X.index.max() + pd.Timedelta(hours=1)
        train = X[(X.index < start) & X["y"].notna() & X[BASE].notna()]
        test = X[(X.index >= start) & (X.index < end)]
        last = model(params).fit(train[cols], train["y"] - train[BASE])
        preds.append(pd.Series(last.predict(test[cols]) + test[BASE], index=test.index))
        if explain:
            sv = shap.TreeExplainer(last).shap_values(test[cols])
            shaps.append(pd.DataFrame(sv, index=test.index, columns=cols))
    return pd.concat(preds), (pd.concat(shaps) if explain else None), last


# --- Evaluation & charts ------------------------------------------------------------------------------

def monthly_table(y: pd.Series, pred: pd.Series) -> pd.DataFrame:
    local_month = y.index.tz_convert(TZ).strftime("%Y-%m")
    rows = [{"Month": month, **metrics(part, pred.reindex(part.index))} for month, part in y.groupby(local_month)]
    return pd.DataFrame(rows)


def plot_monthly(table: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(table["Month"], table["nMAE_%"], color=COLORS[0], width=0.65)
    ax.axhline(10, color=COLORS[7], linewidth=1.2, linestyle="--")
    ax.text(len(table) - 0.5, 10.4, "Target 10 %", color=INK_2, ha="right", fontsize=9)
    ax.set_title("LightGBM: normalised MAE per month (holdout, walk-forward)")
    ax.set_ylabel("nMAE [%]")
    ax.tick_params(axis="x", rotation=45)
    save(fig, "01_nmae_by_month.png")


def plot_weeks(y: pd.Series, preds: dict[str, pd.Series], daily_err: pd.Series) -> None:
    """One good, one typical and one difficult week each: actual vs. forecast."""
    weekly = daily_err.resample("W-SUN").mean().dropna()
    picks = {"good week": weekly.idxmin(), "typical week": (weekly - weekly.median()).abs().idxmin(),
             "difficult week": weekly.idxmax()}
    fig, axes = plt.subplots(3, 1, figsize=(11, 9))
    for ax, (label, end) in zip(axes, picks.items()):
        sl = slice(end - pd.Timedelta(days=7), end)
        idx = y.loc[sl].index
        ax.plot(idx.tz_convert(TZ), y.loc[sl], color=INK, linewidth=1.6, label="Actual (CH day-ahead)")
        for i, (name, p) in enumerate(preds.items()):
            ax.plot(idx.tz_convert(TZ), p.reindex(idx), color=COLORS[i], linewidth=1.2, label=name)
        ax.set_title(f"{label[0].upper() + label[1:]}: {idx.min().tz_convert(TZ):%d.%m.%Y} – {idx.max().tz_convert(TZ):%d.%m.%Y}"
                     f"  (nMAE LightGBM {weekly[end]:.1f} %)")
        ax.set_ylabel("EUR/MWh")
    axes[0].legend(ncols=3, loc="upper left")
    fig.tight_layout()
    save(fig, "02_example_weeks.png")


def plot_error_by_hour(y: pd.Series, preds: dict[str, pd.Series]) -> None:
    hour = y.index.tz_convert(TZ).hour
    fig, ax = plt.subplots(figsize=(9, 4))
    for i, (name, p) in enumerate(preds.items()):
        err = (y - p.reindex(y.index)).abs().groupby(hour).mean()
        ax.plot(err.index, err, color=COLORS[i], marker="o", markersize=4, label=name)
    ax.set_title("Mean absolute error per hour (holdout)")
    ax.set_xlabel("Hour (local time)")
    ax.set_ylabel("MAE [EUR/MWh]")
    ax.set_xticks(range(0, 24, 3))
    ax.legend()
    save(fig, "03_error_by_hour.png")


def plot_shap(sv: pd.DataFrame, X: pd.DataFrame, y: pd.Series, pred: pd.Series) -> None:
    cols = list(sv.columns)
    # 1) Beeswarm of the top features
    shap.summary_plot(sv.to_numpy(), X.loc[sv.index, cols], max_display=20, show=False, plot_size=(9, 8))
    fig = plt.gcf()
    fig.axes[0].set_title("SHAP: influence of the most important features on the price forecast", loc="left")
    fig.axes[0].set_xlabel("SHAP value = contribution to the forecast [EUR/MWh]  (colour: feature value low → high)")
    save(fig, "04_shap_beeswarm.png")

    # 2) Importance per driver group
    groups = sv.abs().T.groupby(driver_group).sum().T.mean().sort_values()
    groups.to_csv(OUT / "shap_driver_groups.csv", header=["mean_abs_shap_eur_mwh"], float_format="%.3f")
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(groups.index, groups.values, color=COLORS[0], height=0.6)
    for i, v in enumerate(groups.values):
        ax.text(v + 0.2, i, f"{v:.1f}", va="center", fontsize=9, color=INK_2)
    ax.set_title("Which market drivers move the forecast? (mean |SHAP|)")
    ax.set_xlabel("Mean contribution to the forecast [EUR/MWh]")
    save(fig, "05_shap_driver_groups.png")

    # 3) Explanation of an example day: lowest midday price in the holdout (typically solar + weekend)
    local = sv.index.tz_convert(TZ)
    midday = y.loc[sv.index][(local.hour >= 11) & (local.hour <= 15)]
    day = midday.groupby(midday.index.tz_convert(TZ).date).mean().idxmin()
    explain_day(sv, X, y, pred, day, "06_explanation_low_price_day.png")
    # and the most expensive evening (scarcity)
    evening = y.loc[sv.index][(local.hour >= 17) & (local.hour <= 21)]
    day = evening.groupby(evening.index.tz_convert(TZ).date).mean().idxmax()
    explain_day(sv, X, y, pred, day, "07_explanation_high_price_day.png")


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def long_date(day) -> str:
    ts = pd.Timestamp(day)
    return f"{WEEKDAYS[ts.day_of_week]} {ts:%d.%m.%Y}"


def explain_day(sv: pd.DataFrame, X: pd.DataFrame, y: pd.Series, pred: pd.Series, day, name: str) -> None:
    local = sv.index.tz_convert(TZ)
    mask = local.date == day
    s = sv[mask]
    hours = local[mask].hour
    grouped = s.T.groupby(driver_group).sum().T
    # max. 6 drivers + "Other" (grey), so that each colour denotes exactly one driver
    top = grouped.abs().sum().sort_values(ascending=False).index[:6]
    grouped = pd.concat([grouped[top], grouped.drop(columns=top).sum(axis=1).rename("Other")], axis=1)
    # Forecast = previous-day price + model expected value + Σ SHAP
    base = float((pred[s.index] - X.loc[s.index, BASE] - s.sum(axis=1)).mean())

    fig, ax = plt.subplots(figsize=(11, 5))
    pos = np.zeros(len(s))
    neg = np.zeros(len(s))
    for i, g in enumerate(grouped.columns):
        v = grouped[g].to_numpy()
        color = "#a3a29c" if g == "Other" else COLORS[i]
        ax.bar(hours, np.where(v > 0, v, 0), bottom=pos, color=color, width=0.8, label=g,
               edgecolor="#fcfcfb", linewidth=0.5)
        ax.bar(hours, np.where(v < 0, v, 0), bottom=neg, color=color, width=0.8,
               edgecolor="#fcfcfb", linewidth=0.5)
        pos += np.where(v > 0, v, 0)
        neg += np.where(v < 0, v, 0)
    ax.axhline(0, color=INK_2, linewidth=0.8)
    ax.set_title(f"Why does the model forecast this profile? {long_date(day)}\n"
                 f"Bars = contribution per driver to the price change vs. previous day (same hour)", fontsize=11)
    ax.set_xlabel("Hour (local time)")
    ax.set_ylabel("Contribution [EUR/MWh]")
    ax.set_xticks(range(0, 24, 2))
    ax.legend(ncols=2, fontsize=8, loc="lower left")
    save(fig, name)

    # Actual vs. forecast of the day as a separate chart (same unit, own axis – no dual-axis chart)
    fig, ax = plt.subplots(figsize=(11, 3))
    ax.plot(hours, y[s.index], color=INK, marker="o", markersize=4, label="Actual")
    ax.plot(hours, pred[s.index], color=COLORS[0], marker="o", markersize=4, label="Forecast LightGBM")
    ax.plot(hours, X.loc[s.index, BASE], color=COLORS[1], linewidth=1.2, label="Previous day (starting point)")
    ax.set_title(f"Actual vs. forecast {pd.Timestamp(day):%d.%m.%Y}")
    ax.set_ylabel("EUR/MWh")
    ax.set_xticks(range(0, 24, 2))
    ax.legend()
    save(fig, name.replace(".png", "_profile.png"))

    # Text explanation for the midday hour (or 19:00 on the high-price day)
    target_hour = 13 if "low" in name else 19
    row = s[hours == target_hour]
    if row.empty:
        return
    contrib = row.iloc[0].sort_values(key=np.abs, ascending=False).head(6)
    t = row.index[0]
    note(f"**{long_date(day)}, {target_hour}:00** – forecast {pred[t]:.1f} EUR/MWh, actual {y[t]:.1f} EUR/MWh. "
         f"Starting point previous day {X.loc[t, BASE]:.1f} EUR/MWh, mean model correction {base:+.1f}; "
         f"most important contributions to the change:\n")
    for feat, v in contrib.items():
        note(f"- `{feat}` = {X.loc[t, feat]:.1f} → {v:+.1f} EUR/MWh ({driver_group(feat)})")
    note("")


def quarter_hour_check(pred: pd.Series) -> None:
    """The target is the 15 min tariff: transfer the hourly forecast to quarter hours and check it."""
    path = PROCESSED / "price_ch_native.csv"
    if not path.exists():
        return
    native = pd.read_csv(path, index_col=0)["price"]
    native.index = pd.to_datetime(native.index, utc=True)
    native = native[native.index >= TEST_START]
    step = native.index.to_series().diff().median()
    if step != pd.Timedelta(minutes=15):
        note(f"CH day-ahead in the holdout at {step} resolution – no separate 15 min evaluation needed.")
        return
    q = pred.reindex(native.index.floor("1h")).set_axis(native.index)
    m = metrics(native, q)
    note(f"**15 min resolution:** hourly forecast transferred to quarter hours → MAE {m['MAE']:.2f} EUR/MWh, "
         f"nMAE {m['nMAE_%']:.1f} % (n = {m['n']}). The additional deviation comes from the "
         f"quarter-hour structure within the hour.")


def main() -> None:
    plot_style()
    df = processed_hourly()
    X = build(df)
    cols = feature_columns(X)
    note("# LightGBM forecast of the CH day-ahead price (24 h ahead)\n")
    note(f"{len(cols)} features, training from {X.index.min():%Y-%m-%d}, holdout from {TEST_START.tz_convert(TZ):%Y-%m-%d}.\n")

    print(f"Hyperparameter search ({TRIALS} candidates × 5 time-series folds) …")
    best, search = tune(X, cols)
    search.to_csv(OUT / "tuning.csv", index=False, float_format="%.4f")
    (OUT / "best_params.json").write_text(json.dumps(best, indent=2, default=str))
    note("## Hyperparameters (TimeSeriesSplit, 5 folds of 60 days, 24 h gap)\n")
    note(f"Best CV MAE: {search['cv_mae'].iloc[0]:.2f} ± {search['cv_mae_std'].iloc[0]:.2f} EUR/MWh\n")
    note("```json\n" + json.dumps(best, indent=2, default=str) + "\n```\n")

    print("Walk-forward holdout …")
    pred, sv, _ = walk_forward(X, cols, best, explain=True)
    X_wx = build(df, use_tso_forecasts=False)
    pred_wx, _, _ = walk_forward(X_wx, feature_columns(X_wx), best)

    y = X["y"][(X.index >= TEST_START) & X["y"].notna()]
    baselines = pd.read_csv(out_dir("baseline") / "predictions.csv", index_col=0)
    baselines.index = pd.to_datetime(baselines.index, utc=True)
    candidates = {"LightGBM": pred, "LightGBM without grid operator forecasts": pred_wx}
    for name in ("Ridge regression", "Profile 14 days", "Persistence 24 h", "Persistence 168 h"):
        if name in baselines:
            candidates[name] = baselines[name]
    rows = [{"Model": k, **metrics(y, v.reindex(y.index))} for k, v in candidates.items()]
    table = pd.DataFrame(rows)
    persistence = table.set_index("Model").loc["Persistence 24 h", "MAE"]
    table["Improvement vs. persistence_%"] = 100 * (1 - table["MAE"] / persistence)
    table.to_csv(OUT / "metrics.csv", index=False, float_format="%.3f")
    pd.DataFrame({"actual": y, "lightgbm": pred.reindex(y.index), "lightgbm_no_tso": pred_wx.reindex(y.index)}) \
        .to_csv(OUT / "predictions.csv")

    note(f"## Holdout result ({y.index.min().tz_convert(TZ):%d.%m.%Y} – {y.index.max().tz_convert(TZ):%d.%m.%Y}, "
         f"{len(y)} hours)\n")
    note(f"Mean absolute price: {y.abs().mean():.1f} EUR/MWh. nMAE = MAE / mean absolute price.\n")
    note(table.to_markdown(index=False, floatfmt=".2f"))
    lgbm = table.iloc[0]
    verdict = "achieved" if lgbm["nMAE_%"] < 10 else "not yet achieved"
    note(f"\n**Target nMAE < 10 %: {verdict}** (LightGBM {lgbm['nMAE_%']:.1f} %).\n")

    mt = monthly_table(y, pred)
    mt.to_csv(OUT / "metrics_monthly.csv", index=False, float_format="%.3f")
    note("### Per month\n")
    note(mt.to_markdown(index=False, floatfmt=".2f"))
    note(f"\n{(mt['nMAE_%'] < 10).sum()} of {len(mt)} months below 10 %.\n")
    quarter_hour_check(pred)

    note("\n## Charts\n")
    plot_monthly(mt)
    daily_err = (y - pred.reindex(y.index)).abs().resample("D").mean() / y.abs().resample("D").mean() * 100
    plot_weeks(y, {"LightGBM": pred, "Persistence 24 h": candidates["Persistence 24 h"]}, daily_err)
    plot_error_by_hour(y, {k: candidates[k] for k in ("LightGBM", "Ridge regression", "Persistence 24 h") if k in candidates})

    note("\n## Explanation with SHAP\n")
    plot_shap(sv, X, X["y"], pred)
    sv.to_csv(OUT / "shap_values.csv", float_format="%.3f")

    # Final model on all data – for later use in the calculator/dashboard
    full = X[X["y"].notna() & X[BASE].notna()]
    final = model(best).fit(full[cols], full["y"] - full[BASE])
    final.booster_.save_model(str(OUT / "lgbm_ch_dayahead.txt"))
    (OUT / "feature_columns.json").write_text(json.dumps({"base": BASE, "features": cols}, indent=2))
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"\n→ {OUT / 'summary.md'}")


if __name__ == "__main__":
    main()
