"""Schritt 06 – Zielwert-Analyse: Welcher nMAE ist als Prognoseziel sinnvoll?

Der nMAE (MAE / mittlerer Absolutpreis) hängt nicht nur vom Modell ab, sondern stark davon, wie
stark die Preise in der Auswertungsperiode schwanken und wie hoch das Preisniveau (der Nenner) ist.
Ein fixes "nMAE < 10 %" ist deshalb nur zusammen mit der Periode aussagekräftig.

Vorgehen:
  1. CH-Day-Ahead-Preise 2015 – heute laden (energy-charts, Cache unter data/raw/price_ch_long/),
     auf Stundenmittel bringen.
  2. Je Jahr und Monat: Niveau, Streuung, Tagesspanne, negative Stunden.
  3. Naive Prognosen ohne jedes Modell → deren nMAE misst die "Schwierigkeit" einer Periode:
       * Persistenz 24 h   – morgen = gestern zur selben Stunde (Baseline des Projekts)
       * Persistenz 168 h  – wie vor 7 Tagen; Referenz für den rMAE nach Lago et al. (2021)
       * Wochen-Naiv       – Mo/Sa/So wie vor 7 Tagen, Di–Fr wie gestern (Lago et al., 3. Variante)
       * Profil 14 Tage    – Mittel derselben Stunde der letzten 14 Tage je Tagtyp (Fallback im Rechner)
  4. Modell aus Schritt 04 im Holdout → rMAE = MAE_Modell / MAE_Persistenz-168-h (Lago et al. 2021, Gl. 9).
     Der rMAE ist (anders als der nMAE) zwischen Perioden und Märkten vergleichbar.
  5. Szenarien: nMAE, den ein Modell mit einem rMAE wie in der Literatur in jedem Jahr erreichen würde.

Aufruf:  .venv/bin/python 06_zielwert.py [--refresh]   (Schritt 04 muss vorher gelaufen sein)
"""

from __future__ import annotations

import importlib
import sys

import numpy as np
import pandas as pd

from common import COLORS, INK_2, OUT, RAW, TEST_START, TZ, out_dir, plot_style, profile_forecast

fetch = importlib.import_module("01_fetch_data")

FIRST_YEAR = 2015  # erstes Jahr mit CH-Day-Ahead-Preisen auf energy-charts
CACHE = RAW / "price_ch_long"
DIR = out_dir("zielwert")
NOW = pd.Timestamp.now(tz="UTC")
REFRESH = "--refresh" in sys.argv
NAIVE = ["Persistenz 24 h", "Persistenz 168 h", "Wochen-Naiv", "Profil 14 Tage"]
REF = "Persistenz 168 h"

# Lago et al. (2021), Tab. 2/3: rMAE (Referenz Persistenz 168 h) der offenen State-of-the-art-Modelle,
# Testperioden je 2 Jahre zwischen 2015 und 2018. Min = DNN-Ensemble, Max = schwächstes LEAR-Modell.
LITERATURE = {"EPEX-DE": (0.377, 0.469), "Nord Pool": (0.403, 0.482), "PJM": (0.439, 0.550),
              "EPEX-FR": (0.533, 0.638), "EPEX-BE": (0.573, 0.682)}
RMAE_SCENARIOS = [0.4, 0.5, 0.6, 0.7]


def load_prices() -> tuple[pd.Series, pd.Series]:
    """Stündliche CH-Day-Ahead-Preise (UTC-Index) und Originalauflösung je Jahr in Minuten."""
    parts, resolution = [], {}
    for year in range(FIRST_YEAR, NOW.year + 1):
        path = CACHE / f"{year}.csv"
        start = pd.Timestamp(f"{year}-01-01", tz=TZ).tz_convert("UTC")
        end = min(pd.Timestamp(f"{year + 1}-01-01", tz=TZ).tz_convert("UTC"), (NOW + pd.Timedelta(days=2)).floor("h"))
        if path.exists() and not (REFRESH or year == NOW.year):
            df = pd.read_csv(path, index_col=0)
            df.index = pd.to_datetime(df.index, utc=True)
        else:
            print(f"  lade {year} …")
            d = fetch.get_json(f"{fetch.EC}/price", {"bzn": "CH", **fetch.ec_dates(start, end)})
            if not d or not d.get("unix_seconds"):
                print(f"    {year}: keine Daten")
                continue
            df = fetch.ec_frame(d["unix_seconds"], {"price": d["price"]})
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(path)
        df = df[(df.index >= start) & (df.index < end)].dropna()
        resolution[year] = int(pd.Series(df.index).diff().dt.total_seconds().div(60).mode().iloc[0])
        parts.append(df["price"])
    raw = pd.concat(parts).sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]
    return raw.resample("1h").mean(), pd.Series(resolution, name="Auflösung_min")


def naive_forecasts(y: pd.Series) -> pd.DataFrame:
    lag24, lag168 = y.shift(24), y.shift(168)
    weekday = y.index.tz_convert(TZ).day_of_week
    weekly = pd.Series(np.where(np.isin(weekday, [0, 5, 6]), lag168, lag24), index=y.index)
    return pd.DataFrame({"Persistenz 24 h": lag24, "Persistenz 168 h": lag168, "Wochen-Naiv": weekly,
                         "Profil 14 Tage": profile_forecast(y)})


def period_stats(frame: pd.DataFrame) -> pd.Series:
    """Kennzahlen einer Periode; frame enthält y, die naiven Prognosen und das lokale Datum."""
    y = frame["y"]
    mean_abs = y.abs().mean()
    spread = frame.groupby("date")["y"].agg(lambda s: s.max() - s.min()).mean()
    out = {
        "Stunden": len(y), "Mittel": y.mean(), "Ø |Preis|": mean_abs, "Std": y.std(),
        "rel. Std_%": 100 * y.std() / mean_abs,
        "Ø Tagesspanne": spread, "rel. Tagesspanne_%": 100 * spread / mean_abs,
        "Std. negativ": int((y < 0).sum()),
    }
    for name in NAIVE:
        err = (y - frame[name]).abs()
        out[f"MAE {name}"] = err.mean()
        out[f"nMAE {name}_%"] = 100 * err.mean() / mean_abs
    return pd.Series(out)


def grouped(frame: pd.DataFrame, key: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({k: period_stats(g) for k, g in frame.groupby(key)}).T


def model_comparison(naive: pd.DataFrame) -> tuple[pd.DataFrame, dict] | None:
    """nMAE und rMAE des LightGBM-Modells im Holdout (aus Schritt 04), gesamt und je Monat."""
    path = OUT / "model" / "predictions.csv"
    if not path.exists():
        print("  output/model/predictions.csv fehlt – Modellvergleich übersprungen (zuerst 04_model.py)")
        return None
    pred = pd.read_csv(path, index_col=0)
    pred.index = pd.to_datetime(pred.index, utc=True)
    frame = pd.DataFrame({"y": pred["actual"], "LightGBM": pred["lightgbm"],
                          "LightGBM ohne NB-Prognosen": pred["lightgbm_no_tso"]}).join(naive).dropna()
    frame = frame[frame.index >= TEST_START]

    def row(g: pd.DataFrame) -> pd.Series:
        mean_abs = g["y"].abs().mean()
        mae = {c: (g["y"] - g[c]).abs().mean() for c in ["LightGBM", "LightGBM ohne NB-Prognosen", *NAIVE]}
        return pd.Series({
            "Stunden": len(g), "Ø |Preis|": mean_abs,
            "nMAE LightGBM_%": 100 * mae["LightGBM"] / mean_abs,
            "nMAE Persistenz 24 h_%": 100 * mae["Persistenz 24 h"] / mean_abs,
            "nMAE Persistenz 168 h_%": 100 * mae[REF] / mean_abs,
            "rMAE LightGBM": mae["LightGBM"] / mae[REF],
            "rMAE ohne NB-Prognosen": mae["LightGBM ohne NB-Prognosen"] / mae[REF],
            "LightGBM / Persistenz 24 h": mae["LightGBM"] / mae["Persistenz 24 h"],
        })

    month = frame.index.tz_convert(TZ).strftime("%Y-%m")
    monthly = pd.DataFrame({m: row(g) for m, g in frame.groupby(month)}).T
    return monthly, row(frame).to_dict()


def year_labels(index) -> list[str]:
    return [f"{i}*" if int(i) == NOW.year else str(i) for i in index]


def plot_years(years: pd.DataFrame, model_rmae: float | None) -> None:
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 9), sharex=True, gridspec_kw={"height_ratios": [1, 1.4]})
    x = np.arange(len(years))
    ax1.bar(x - 0.2, years["Ø |Preis|"], 0.4, label="Ø |Preis| (Nenner des nMAE)", color=COLORS[0])
    ax1.bar(x + 0.2, years["Ø Tagesspanne"], 0.4, label="Ø Tagesspanne (max − min)", color=COLORS[1])
    ax1.set_ylabel("EUR/MWh")
    ax1.set_title("Preisniveau und Schwankung des CH-Day-Ahead-Preises je Jahr")
    ax1.legend(loc="upper left")

    ref = years[f"nMAE {REF}_%"]
    lo, hi = min(v[0] for v in LITERATURE.values()), max(v[1] for v in LITERATURE.values())
    ax2.plot(x, ref, "s-", label="naiv: Persistenz 168 h (rMAE-Referenz)", color=COLORS[4])
    ax2.plot(x, years["nMAE Persistenz 24 h_%"], "o--", lw=1, label="naiv: Persistenz 24 h", color=COLORS[3])
    ax2.fill_between(x, lo * ref, hi * ref, color=COLORS[2], alpha=0.15,
                     label=f"State of the art (rMAE {lo:.2f}–{hi:.2f}, Lago et al. 2021)")
    if model_rmae is not None:
        ax2.plot(x, model_rmae * ref, "o-", color=COLORS[2], label=f"unser Modell, Holdout-rMAE {model_rmae:.2f} (hochgerechnet)")
    ax2.axhline(10, color=INK_2, lw=1, ls=":", label="bisheriges Ziel 10 %")
    ax2.set_ylabel("nMAE [%]")
    ax2.set_ylim(0, ref.max() * 1.45)
    ax2.set_title("nMAE je Jahr: naive Prognosen und erreichbarer Bereich")
    ax2.set_xticks(x, year_labels(years.index))
    ax2.legend(loc="upper left", ncols=2, fontsize=8)
    fig.savefig(DIR / "01_niveau_volatilitaet_nmae_je_jahr.png")
    plt.close(fig)


def plot_months(months: pd.DataFrame) -> float:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 6))
    year = months.index.str[:4].astype(int)
    xcol, ycol = "rel. Tagesspanne_%", f"nMAE {REF}_%"
    cmap = plt.get_cmap("viridis", year.nunique())
    for i, yr in enumerate(sorted(year.unique())):
        sel = months[year == yr]
        ax.scatter(sel[xcol], sel[ycol], s=22, color=cmap(i), label=str(yr))
    r = np.corrcoef(months[xcol], months[ycol])[0, 1]
    slope, icpt = np.polyfit(months[xcol], months[ycol], 1)
    xs = np.linspace(months[xcol].min(), months[xcol].max(), 50)
    ax.plot(xs, slope * xs + icpt, color=INK_2, lw=1, ls="--", label=f"linearer Fit (r = {r:.2f})")
    ax.axhline(10, color=INK_2, lw=1, ls=":")
    ax.set_xlabel("relative Tagesspanne = Ø (max − min) je Tag / Ø |Preis|  [%]")
    ax.set_ylabel(f"nMAE {REF} [%]")
    ax.set_title("Je Monat: Je stärker die Preise relativ zum Niveau schwanken, desto grösser der nMAE")
    ax.legend(ncols=3, fontsize=8)
    fig.savefig(DIR / "02_nmae_vs_relative_schwankung_je_monat.png")
    plt.close(fig)
    return r


def plot_model(monthly: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True)
    x = np.arange(len(monthly))
    ax1.bar(x - 0.27, monthly["nMAE Persistenz 168 h_%"], 0.27, label="Persistenz 168 h", color=COLORS[4])
    ax1.bar(x, monthly["nMAE Persistenz 24 h_%"], 0.27, label="Persistenz 24 h", color=COLORS[3])
    ax1.bar(x + 0.27, monthly["nMAE LightGBM_%"], 0.27, label="LightGBM", color=COLORS[2])
    ax1.axhline(10, color=INK_2, lw=1, ls=":", label="10 %")
    ax1.set_ylabel("nMAE [%]")
    ax1.set_title("Holdout je Monat: Der nMAE schwankt stark mit der Saison …")
    ax1.legend(ncols=2)
    lo, hi = min(v[0] for v in LITERATURE.values()), max(v[1] for v in LITERATURE.values())
    ax2.axhspan(lo, hi, color=COLORS[2], alpha=0.12, label=f"State of the art {lo:.2f}–{hi:.2f} (Lago et al. 2021)")
    ax2.plot(x, monthly["rMAE LightGBM"], "o-", color=COLORS[2], label="LightGBM")
    ax2.plot(x, monthly["rMAE ohne NB-Prognosen"], "s--", lw=1, color=COLORS[6], label="LightGBM ohne Netzbetreiber-Prognosen")
    ax2.axhline(1, color=INK_2, lw=1, ls=":")
    ax2.set_ylim(0, max(1.1, monthly["rMAE ohne NB-Prognosen"].max() * 1.08))
    ax2.set_ylabel("rMAE (vs. Persistenz 168 h)")
    ax2.set_title("… der rMAE (Modell relativ zur naiven Prognose) bleibt deutlich stabiler")
    ax2.set_xticks(x, monthly.index, rotation=45)
    ax2.legend(loc="upper left", fontsize=8)
    fig.savefig(DIR / "03_holdout_nmae_rmae_je_monat.png")
    plt.close(fig)


def main() -> None:
    plot_style()
    y, resolution = load_prices()
    naive = naive_forecasts(y)
    frame = naive.assign(y=y, date=y.index.tz_convert(TZ).normalize()).dropna()
    flocal = frame.index.tz_convert(TZ)

    years = grouped(frame, pd.Series(flocal.year, index=frame.index)).join(resolution)
    years.index = years.index.astype(int)
    months = grouped(frame, pd.Series(flocal.strftime("%Y-%m"), index=frame.index))
    total = period_stats(frame)
    years.to_csv(DIR / "kennzahlen_je_jahr.csv", float_format="%.3f")
    months.to_csv(DIR / "kennzahlen_je_monat.csv", float_format="%.3f")

    comparison = model_comparison(naive)
    model_rmae = None
    if comparison:
        monthly, overall = comparison
        monthly.to_csv(DIR / "holdout_je_monat.csv", float_format="%.3f")
        model_rmae = overall["rMAE LightGBM"]
        plot_model(monthly)

    plot_years(years, model_rmae)
    r_month = plot_months(months)

    ref = years[f"nMAE {REF}_%"]
    scen = pd.DataFrame({f"rMAE {r}": r * ref for r in RMAE_SCENARIOS})
    if model_rmae is not None:
        scen.insert(0, f"unser Modell (rMAE {model_rmae:.2f})", model_rmae * ref)
    scen.insert(0, "naiv (Persistenz 168 h)", ref)
    scen.loc["Ø Jahre"] = scen.mean()
    scen.to_csv(DIR / "szenarien_nmae_je_jahr.csv", float_format="%.2f")

    show = years[["Stunden", "Auflösung_min", "Mittel", "Ø |Preis|", "Std", "Ø Tagesspanne",
                  "rel. Tagesspanne_%", "Std. negativ", "nMAE Persistenz 24 h_%", "nMAE Persistenz 168 h_%",
                  "nMAE Wochen-Naiv_%", "nMAE Profil 14 Tage_%"]].copy()
    show.index = year_labels(show.index)
    lit = pd.DataFrame(LITERATURE, index=["rMAE min (DNN-Ensemble)", "rMAE max (schwächstes LEAR)"]).T
    lines = [
        "# Zielwert-Analyse: Welcher nMAE ist für die 24h-Prognose sinnvoll?",
        "",
        f"CH-Day-Ahead (energy-charts), {frame.index.min().tz_convert(TZ):%d.%m.%Y} – "
        f"{frame.index.max().tz_convert(TZ):%d.%m.%Y}, {len(frame)} Stunden, stündlich gemittelt. "
        f"\\* = laufendes Jahr (unvollständig).",
        "",
        "- nMAE = MAE / Ø |Preis|",
        f"- rMAE = MAE_Modell / MAE_naiv mit naiv = {REF} (p_d−7,h), Definition nach Lago et al. (2021), Gl. 9",
        "",
        "## Niveau, Schwankung und Schwierigkeit je Jahr",
        "",
        show.to_markdown(floatfmt=".1f"),
        "",
        "Gesamter Zeitraum: nMAE " + ", ".join(f"{n} {total[f'nMAE {n}_%']:.1f} %" for n in NAIVE) + ".",
        "",
        f"Je Monat korreliert der nMAE der {REF} mit der relativen Tagesspanne mit r = {r_month:.2f} "
        f"(Spannweite der Monate: {months[f'nMAE {REF}_%'].min():.1f} – {months[f'nMAE {REF}_%'].max():.1f} %).",
        "",
        "![01](01_niveau_volatilitaet_nmae_je_jahr.png)",
        "",
        "![02](02_nmae_vs_relative_schwankung_je_monat.png)",
        "",
        "## Literatur: rMAE von State-of-the-art-Modellen",
        "",
        "Lago, Marcjasz, De Schutter & Weron (2021), Applied Energy 293, 116983, Tab. 2 und 3 "
        "(je 2 Jahre Test zwischen 2015 und 2018):",
        "",
        lit.to_markdown(floatfmt=".3f"),
        "",
    ]
    if comparison:
        lines += [
            "## Unser Modell im Holdout: nMAE vs. rMAE",
            "",
            f"Gesamt: nMAE LightGBM {overall['nMAE LightGBM_%']:.1f} %, {REF} {overall['nMAE Persistenz 168 h_%']:.1f} % "
            f"→ **rMAE {overall['rMAE LightGBM']:.2f}** (ohne Netzbetreiber-Prognosen {overall['rMAE ohne NB-Prognosen']:.2f}; "
            f"relativ zu Persistenz 24 h {overall['LightGBM / Persistenz 24 h']:.2f}).",
            "",
            f"Streuung je Monat: nMAE LightGBM {monthly['nMAE LightGBM_%'].min():.1f} – "
            f"{monthly['nMAE LightGBM_%'].max():.1f} % (Faktor "
            f"{monthly['nMAE LightGBM_%'].max() / monthly['nMAE LightGBM_%'].min():.1f}), rMAE "
            f"{monthly['rMAE LightGBM'].min():.2f} – {monthly['rMAE LightGBM'].max():.2f}.",
            "",
            monthly.to_markdown(floatfmt=".2f"),
            "",
            "![03](03_holdout_nmae_rmae_je_monat.png)",
            "",
        ]
    lines += [
        "## Szenarien: erreichbarer nMAE je Jahr bei gegebenem rMAE",
        "",
        f"nMAE ≈ rMAE × nMAE({REF}) des jeweiligen Jahres.",
        "",
        scen.rename(index=lambda i: f"{i}*" if str(i) == str(NOW.year) else str(i)).to_markdown(floatfmt=".1f"),
        "",
    ]
    (DIR / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
