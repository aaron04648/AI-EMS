"""Schritt 04 – LightGBM-Prognose, Hyperparameter-Tuning und SHAP-Erklärung.

Zielgrösse des Modells ist die Änderung ggü. dem Vortag zur selben Stunde (y − lag_ch_24); die
Prognose ist Vortagespreis + Modell. So muss das Baum-Modell das (stark schwankende) Preisniveau nicht
extrapolieren – das Niveau steckt im bekannten Vortagespreis.

1. Tuning: Zufallssuche über LightGBM-Parameter mit TimeSeriesSplit (expandierendes Fenster,
   24 h Lücke zwischen Training und Validierung – nie zufällig mischen) auf den Daten vor TEST_START.
2. Holdout: Walk-Forward – für jeden Monat ab TEST_START wird mit allen Daten davor neu trainiert
   und der Monat prognostiziert (wie im echten Betrieb).
3. Robustheit: dieselbe Auswertung ohne Netzbetreiber-Prognosen (nur Wetter + Lags).
4. SHAP: Beitrag jedes Features bzw. Treibers zu jeder einzelnen Stundenprognose.

Aufruf:  .venv/bin/python 04_model.py [--trials 30]
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
BASE = "lag_ch_24"  # Preis gestern, gleiche Stunde (zum Prognosezeitpunkt bekannt)
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


# --- Walk-Forward-Holdout ---------------------------------------------------------------------------

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


# --- Auswertung & Grafiken ----------------------------------------------------------------------------

def monthly_table(y: pd.Series, pred: pd.Series) -> pd.DataFrame:
    local_month = y.index.tz_convert(TZ).strftime("%Y-%m")
    rows = [{"Monat": month, **metrics(part, pred.reindex(part.index))} for month, part in y.groupby(local_month)]
    return pd.DataFrame(rows)


def plot_monthly(table: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(table["Monat"], table["nMAE_%"], color=COLORS[0], width=0.65)
    ax.axhline(10, color=COLORS[7], linewidth=1.2, linestyle="--")
    ax.text(len(table) - 0.5, 10.4, "Ziel 10 %", color=INK_2, ha="right", fontsize=9)
    ax.set_title("LightGBM: normierter MAE je Monat (Holdout, Walk-Forward)")
    ax.set_ylabel("nMAE [%]")
    ax.tick_params(axis="x", rotation=45)
    save(fig, "01_nmae_je_monat.png")


def plot_weeks(y: pd.Series, preds: dict[str, pd.Series], daily_err: pd.Series) -> None:
    """Je eine gute, typische und schwierige Woche: Ist vs. Prognose."""
    weekly = daily_err.resample("W-SUN").mean().dropna()
    picks = {"gute Woche": weekly.idxmin(), "typische Woche": (weekly - weekly.median()).abs().idxmin(),
             "schwierige Woche": weekly.idxmax()}
    fig, axes = plt.subplots(3, 1, figsize=(11, 9))
    for ax, (label, end) in zip(axes, picks.items()):
        sl = slice(end - pd.Timedelta(days=7), end)
        idx = y.loc[sl].index
        ax.plot(idx.tz_convert(TZ), y.loc[sl], color=INK, linewidth=1.6, label="Ist (CH Day-Ahead)")
        for i, (name, p) in enumerate(preds.items()):
            ax.plot(idx.tz_convert(TZ), p.reindex(idx), color=COLORS[i], linewidth=1.2, label=name)
        ax.set_title(f"{label[0].upper() + label[1:]}: {idx.min().tz_convert(TZ):%d.%m.%Y} – {idx.max().tz_convert(TZ):%d.%m.%Y}"
                     f"  (nMAE LightGBM {weekly[end]:.1f} %)")
        ax.set_ylabel("EUR/MWh")
    axes[0].legend(ncols=3, loc="upper left")
    fig.tight_layout()
    save(fig, "02_beispielwochen.png")


def plot_error_by_hour(y: pd.Series, preds: dict[str, pd.Series]) -> None:
    hour = y.index.tz_convert(TZ).hour
    fig, ax = plt.subplots(figsize=(9, 4))
    for i, (name, p) in enumerate(preds.items()):
        err = (y - p.reindex(y.index)).abs().groupby(hour).mean()
        ax.plot(err.index, err, color=COLORS[i], marker="o", markersize=4, label=name)
    ax.set_title("Mittlerer absoluter Fehler je Stunde (Holdout)")
    ax.set_xlabel("Stunde (Lokalzeit)")
    ax.set_ylabel("MAE [EUR/MWh]")
    ax.set_xticks(range(0, 24, 3))
    ax.legend()
    save(fig, "03_fehler_je_stunde.png")


def plot_shap(sv: pd.DataFrame, X: pd.DataFrame, y: pd.Series, pred: pd.Series) -> None:
    cols = list(sv.columns)
    # 1) Beeswarm der Top-Features
    shap.summary_plot(sv.to_numpy(), X.loc[sv.index, cols], max_display=20, show=False, plot_size=(9, 8))
    fig = plt.gcf()
    fig.axes[0].set_title("SHAP: Einfluss der wichtigsten Features auf die Preisprognose", loc="left")
    fig.axes[0].set_xlabel("SHAP-Wert = Beitrag zur Prognose [EUR/MWh]  (Farbe: Featurewert tief → hoch)")
    save(fig, "04_shap_beeswarm.png")

    # 2) Bedeutung je Treibergruppe
    groups = sv.abs().T.groupby(driver_group).sum().T.mean().sort_values()
    groups.to_csv(OUT / "shap_treibergruppen.csv", header=["mean_abs_shap_eur_mwh"], float_format="%.3f")
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(groups.index, groups.values, color=COLORS[0], height=0.6)
    for i, v in enumerate(groups.values):
        ax.text(v + 0.2, i, f"{v:.1f}", va="center", fontsize=9, color=INK_2)
    ax.set_title("Welche Markttreiber bewegen die Prognose? (Mittel |SHAP|)")
    ax.set_xlabel("Mittlerer Beitrag zur Prognose [EUR/MWh]")
    save(fig, "05_shap_treibergruppen.png")

    # 3) Erklärung eines Beispieltags: tiefster Mittagspreis im Holdout (typisch Solar + Wochenende)
    local = sv.index.tz_convert(TZ)
    midday = y.loc[sv.index][(local.hour >= 11) & (local.hour <= 15)]
    day = midday.groupby(midday.index.tz_convert(TZ).date).mean().idxmin()
    explain_day(sv, X, y, pred, day, "06_erklaerung_tiefpreis_tag.png")
    # und der teuerste Abend (Knappheit)
    evening = y.loc[sv.index][(local.hour >= 17) & (local.hour <= 21)]
    day = evening.groupby(evening.index.tz_convert(TZ).date).mean().idxmax()
    explain_day(sv, X, y, pred, day, "07_erklaerung_hochpreis_tag.png")


WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def de_date(day) -> str:
    ts = pd.Timestamp(day)
    return f"{WEEKDAYS[ts.day_of_week]} {ts:%d.%m.%Y}"


def explain_day(sv: pd.DataFrame, X: pd.DataFrame, y: pd.Series, pred: pd.Series, day, name: str) -> None:
    local = sv.index.tz_convert(TZ)
    mask = local.date == day
    s = sv[mask]
    hours = local[mask].hour
    grouped = s.T.groupby(driver_group).sum().T
    # max. 6 Treiber + "Übrige" (grau), damit jede Farbe genau einen Treiber bezeichnet
    top = grouped.abs().sum().sort_values(ascending=False).index[:6]
    grouped = pd.concat([grouped[top], grouped.drop(columns=top).sum(axis=1).rename("Übrige")], axis=1)
    # Prognose = Vortagespreis + Modell-Erwartungswert + Σ SHAP
    base = float((pred[s.index] - X.loc[s.index, BASE] - s.sum(axis=1)).mean())

    fig, ax = plt.subplots(figsize=(11, 5))
    pos = np.zeros(len(s))
    neg = np.zeros(len(s))
    for i, g in enumerate(grouped.columns):
        v = grouped[g].to_numpy()
        color = "#a3a29c" if g == "Übrige" else COLORS[i]
        ax.bar(hours, np.where(v > 0, v, 0), bottom=pos, color=color, width=0.8, label=g,
               edgecolor="#fcfcfb", linewidth=0.5)
        ax.bar(hours, np.where(v < 0, v, 0), bottom=neg, color=color, width=0.8,
               edgecolor="#fcfcfb", linewidth=0.5)
        pos += np.where(v > 0, v, 0)
        neg += np.where(v < 0, v, 0)
    ax.axhline(0, color=INK_2, linewidth=0.8)
    ax.set_title(f"Warum prognostiziert das Modell diesen Verlauf? {de_date(day)}\n"
                 f"Balken = Beitrag je Treiber zur Preisänderung ggü. Vortag (gleiche Stunde)", fontsize=11)
    ax.set_xlabel("Stunde (Lokalzeit)")
    ax.set_ylabel("Beitrag [EUR/MWh]")
    ax.set_xticks(range(0, 24, 2))
    ax.legend(ncols=2, fontsize=8, loc="lower left")
    save(fig, name)

    # Ist vs. Prognose des Tages als eigene Grafik (gleiche Einheit, eigene Achse – kein Doppelachsen-Chart)
    fig, ax = plt.subplots(figsize=(11, 3))
    ax.plot(hours, y[s.index], color=INK, marker="o", markersize=4, label="Ist")
    ax.plot(hours, pred[s.index], color=COLORS[0], marker="o", markersize=4, label="Prognose LightGBM")
    ax.plot(hours, X.loc[s.index, BASE], color=COLORS[1], linewidth=1.2, label="Vortag (Ausgangspunkt)")
    ax.set_title(f"Ist vs. Prognose {pd.Timestamp(day):%d.%m.%Y}")
    ax.set_ylabel("EUR/MWh")
    ax.set_xticks(range(0, 24, 2))
    ax.legend()
    save(fig, name.replace(".png", "_verlauf.png"))

    # Text-Erklärung für die Mittagsstunde (bzw. 19 Uhr am Hochpreistag)
    target_hour = 13 if "tief" in name else 19
    row = s[hours == target_hour]
    if row.empty:
        return
    contrib = row.iloc[0].sort_values(key=np.abs, ascending=False).head(6)
    t = row.index[0]
    note(f"**{de_date(day)}, {target_hour}:00 Uhr** – Prognose {pred[t]:.1f} EUR/MWh, Ist {y[t]:.1f} EUR/MWh. "
         f"Ausgangspunkt Vortag {X.loc[t, BASE]:.1f} EUR/MWh, mittlere Modellkorrektur {base:+.1f}; "
         f"wichtigste Beiträge zur Änderung:\n")
    for feat, v in contrib.items():
        note(f"- `{feat}` = {X.loc[t, feat]:.1f} → {v:+.1f} EUR/MWh ({driver_group(feat)})")
    note("")


def quarter_hour_check(pred: pd.Series) -> None:
    """Zielgrösse ist der 15-min-Tarif: Stundenprognose auf die Viertelstunden übertragen und prüfen."""
    path = PROCESSED / "price_ch_native.csv"
    if not path.exists():
        return
    native = pd.read_csv(path, index_col=0)["price"]
    native.index = pd.to_datetime(native.index, utc=True)
    native = native[native.index >= TEST_START]
    step = native.index.to_series().diff().median()
    if step != pd.Timedelta(minutes=15):
        note(f"CH-Day-Ahead im Holdout in {step} Auflösung – keine separate 15-min-Auswertung nötig.")
        return
    q = pred.reindex(native.index.floor("1h")).set_axis(native.index)
    m = metrics(native, q)
    note(f"**15-min-Auflösung:** Stundenprognose auf Viertelstunden übertragen → MAE {m['MAE']:.2f} EUR/MWh, "
         f"nMAE {m['nMAE_%']:.1f} % (n = {m['n']}). Die zusätzliche Abweichung stammt aus der "
         f"Viertelstunden-Struktur innerhalb der Stunde.")


def main() -> None:
    plot_style()
    df = processed_hourly()
    X = build(df)
    cols = feature_columns(X)
    note("# LightGBM-Prognose des CH-Day-Ahead-Preises (24 h im Voraus)\n")
    note(f"{len(cols)} Features, Training ab {X.index.min():%Y-%m-%d}, Holdout ab {TEST_START.tz_convert(TZ):%Y-%m-%d}.\n")

    print(f"Hyperparameter-Suche ({TRIALS} Kandidaten × 5 Zeitreihen-Folds) …")
    best, search = tune(X, cols)
    search.to_csv(OUT / "tuning.csv", index=False, float_format="%.4f")
    (OUT / "best_params.json").write_text(json.dumps(best, indent=2, default=str))
    note("## Hyperparameter (TimeSeriesSplit, 5 Folds à 60 Tage, 24 h Lücke)\n")
    note(f"Bester CV-MAE: {search['cv_mae'].iloc[0]:.2f} ± {search['cv_mae_std'].iloc[0]:.2f} EUR/MWh\n")
    note("```json\n" + json.dumps(best, indent=2, default=str) + "\n```\n")

    print("Walk-Forward-Holdout …")
    pred, sv, _ = walk_forward(X, cols, best, explain=True)
    X_wx = build(df, use_tso_forecasts=False)
    pred_wx, _, _ = walk_forward(X_wx, feature_columns(X_wx), best)

    y = X["y"][(X.index >= TEST_START) & X["y"].notna()]
    baselines = pd.read_csv(out_dir("baseline") / "predictions.csv", index_col=0)
    baselines.index = pd.to_datetime(baselines.index, utc=True)
    candidates = {"LightGBM": pred, "LightGBM ohne Netzbetreiber-Prognosen": pred_wx}
    for name in ("Ridge-Regression", "Profil 14 Tage", "Persistenz 24 h", "Persistenz 168 h"):
        if name in baselines:
            candidates[name] = baselines[name]
    rows = [{"Modell": k, **metrics(y, v.reindex(y.index))} for k, v in candidates.items()]
    table = pd.DataFrame(rows)
    persistence = table.set_index("Modell").loc["Persistenz 24 h", "MAE"]
    table["Verbesserung ggü. Persistenz_%"] = 100 * (1 - table["MAE"] / persistence)
    table.to_csv(OUT / "metrics.csv", index=False, float_format="%.3f")
    pd.DataFrame({"actual": y, "lightgbm": pred.reindex(y.index), "lightgbm_no_tso": pred_wx.reindex(y.index)}) \
        .to_csv(OUT / "predictions.csv")

    note(f"## Ergebnis Holdout ({y.index.min().tz_convert(TZ):%d.%m.%Y} – {y.index.max().tz_convert(TZ):%d.%m.%Y}, "
         f"{len(y)} Stunden)\n")
    note(f"Mittlerer Absolutpreis: {y.abs().mean():.1f} EUR/MWh. nMAE = MAE / mittlerer Absolutpreis.\n")
    note(table.to_markdown(index=False, floatfmt=".2f"))
    lgbm = table.iloc[0]
    verdict = "erreicht" if lgbm["nMAE_%"] < 10 else "noch nicht erreicht"
    note(f"\n**Ziel nMAE < 10 %: {verdict}** (LightGBM {lgbm['nMAE_%']:.1f} %).\n")

    mt = monthly_table(y, pred)
    mt.to_csv(OUT / "metrics_monthly.csv", index=False, float_format="%.3f")
    note("### Je Monat\n")
    note(mt.to_markdown(index=False, floatfmt=".2f"))
    note(f"\n{(mt['nMAE_%'] < 10).sum()} von {len(mt)} Monaten unter 10 %.\n")
    quarter_hour_check(pred)

    note("\n## Grafiken\n")
    plot_monthly(mt)
    daily_err = (y - pred.reindex(y.index)).abs().resample("D").mean() / y.abs().resample("D").mean() * 100
    plot_weeks(y, {"LightGBM": pred, "Persistenz 24 h": candidates["Persistenz 24 h"]}, daily_err)
    plot_error_by_hour(y, {k: candidates[k] for k in ("LightGBM", "Ridge-Regression", "Persistenz 24 h") if k in candidates})

    note("\n## Erklärung mit SHAP\n")
    plot_shap(sv, X, X["y"], pred)
    sv.to_csv(OUT / "shap_values.csv", float_format="%.3f")

    # Finales Modell auf allen Daten – für einen späteren Einsatz im Rechner/Dashboard
    full = X[X["y"].notna() & X[BASE].notna()]
    final = model(best).fit(full[cols], full["y"] - full[BASE])
    final.booster_.save_model(str(OUT / "lgbm_ch_dayahead.txt"))
    (OUT / "feature_columns.json").write_text(json.dumps({"base": BASE, "features": cols}, indent=2))
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"\n→ {OUT / 'summary.md'}")


if __name__ == "__main__":
    main()
