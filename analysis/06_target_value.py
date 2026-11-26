"""Step 06 – target value analysis: which nMAE is a sensible forecast target?

The nMAE (MAE / mean absolute price) does not only depend on the model, but strongly on how much
prices fluctuate in the evaluation period and how high the price level (the denominator) is.
A fixed "nMAE < 10 %" is therefore only meaningful together with the period.

Procedure:
  1. Load CH day-ahead prices 2015 – today (energy-charts, cache under data/raw/price_ch_long/),
     convert to hourly means.
  2. Per year and month: level, dispersion, daily spread, negative hours.
  3. Naive forecasts without any model → their nMAE measures the "difficulty" of a period:
       * Persistence 24 h   – tomorrow = yesterday at the same hour (project baseline)
       * Persistence 168 h  – as 7 days ago; reference for the rMAE according to Lago et al. (2021)
       * Weekly naive       – Mon/Sat/Sun as 7 days ago, Tue–Fri as yesterday (Lago et al., 3rd variant)
       * Profile 14 days    – mean of the same hour over the last 14 days per day type (fallback in the calculator)
  4. Model from step 04 in the holdout → rMAE = MAE_model / MAE_persistence-168-h (Lago et al. 2021, eq. 9).
     Unlike the nMAE, the rMAE is comparable between periods and markets.
  5. Scenarios: nMAE that a model with a literature-like rMAE would achieve in each year.

Usage:  .venv/bin/python 06_target_value.py [--refresh]   (step 04 must have run before)
"""

from __future__ import annotations

import importlib
import sys

import numpy as np
import pandas as pd

from common import COLORS, INK_2, OUT, RAW, TEST_START, TZ, out_dir, plot_style, profile_forecast

fetch = importlib.import_module("01_fetch_data")

FIRST_YEAR = 2015  # first year with CH day-ahead prices on energy-charts
CACHE = RAW / "price_ch_long"
DIR = out_dir("target_value")
NOW = pd.Timestamp.now(tz="UTC")
REFRESH = "--refresh" in sys.argv
NAIVE = ["Persistence 24 h", "Persistence 168 h", "Weekly naive", "Profile 14 days"]
REF = "Persistence 168 h"

# Lago et al. (2021), Tab. 2/3: rMAE (reference persistence 168 h) of the open state-of-the-art models,
# test periods of 2 years each between 2015 and 2018. Min = DNN ensemble, max = weakest LEAR model.
LITERATURE = {"EPEX-DE": (0.377, 0.469), "Nord Pool": (0.403, 0.482), "PJM": (0.439, 0.550),
              "EPEX-FR": (0.533, 0.638), "EPEX-BE": (0.573, 0.682)}
RMAE_SCENARIOS = [0.4, 0.5, 0.6, 0.7]


def load_prices() -> tuple[pd.Series, pd.Series]:
    """Hourly CH day-ahead prices (UTC index) and original resolution per year in minutes."""
    parts, resolution = [], {}
    for year in range(FIRST_YEAR, NOW.year + 1):
        path = CACHE / f"{year}.csv"
        start = pd.Timestamp(f"{year}-01-01", tz=TZ).tz_convert("UTC")
        end = min(pd.Timestamp(f"{year + 1}-01-01", tz=TZ).tz_convert("UTC"), (NOW + pd.Timedelta(days=2)).floor("h"))
        if path.exists() and not (REFRESH or year == NOW.year):
            df = pd.read_csv(path, index_col=0)
            df.index = pd.to_datetime(df.index, utc=True)
        else:
            print(f"  loading {year} …")
            d = fetch.get_json(f"{fetch.EC}/price", {"bzn": "CH", **fetch.ec_dates(start, end)})
            if not d or not d.get("unix_seconds"):
                print(f"    {year}: no data")
                continue
            df = fetch.ec_frame(d["unix_seconds"], {"price": d["price"]})
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(path)
        df = df[(df.index >= start) & (df.index < end)].dropna()
        resolution[year] = int(pd.Series(df.index).diff().dt.total_seconds().div(60).mode().iloc[0])
        parts.append(df["price"])
    raw = pd.concat(parts).sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]
    return raw.resample("1h").mean(), pd.Series(resolution, name="Resolution_min")


def naive_forecasts(y: pd.Series) -> pd.DataFrame:
    lag24, lag168 = y.shift(24), y.shift(168)
    weekday = y.index.tz_convert(TZ).day_of_week
    weekly = pd.Series(np.where(np.isin(weekday, [0, 5, 6]), lag168, lag24), index=y.index)
    return pd.DataFrame({"Persistence 24 h": lag24, "Persistence 168 h": lag168, "Weekly naive": weekly,
                         "Profile 14 days": profile_forecast(y)})


def period_stats(frame: pd.DataFrame) -> pd.Series:
    """Statistics of a period; frame contains y, the naive forecasts and the local date."""
    y = frame["y"]
    mean_abs = y.abs().mean()
    spread = frame.groupby("date")["y"].agg(lambda s: s.max() - s.min()).mean()
    out = {
        "Hours": len(y), "Mean": y.mean(), "Avg |price|": mean_abs, "Std": y.std(),
        "rel. Std_%": 100 * y.std() / mean_abs,
        "Avg daily spread": spread, "rel. daily spread_%": 100 * spread / mean_abs,
        "Neg. hours": int((y < 0).sum()),
    }
    for name in NAIVE:
        err = (y - frame[name]).abs()
        out[f"MAE {name}"] = err.mean()
        out[f"nMAE {name}_%"] = 100 * err.mean() / mean_abs
    return pd.Series(out)


def grouped(frame: pd.DataFrame, key: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({k: period_stats(g) for k, g in frame.groupby(key)}).T


def model_comparison(naive: pd.DataFrame) -> tuple[pd.DataFrame, dict] | None:
    """nMAE and rMAE of the LightGBM model in the holdout (from step 04), overall and per month."""
    path = OUT / "model" / "predictions.csv"
    if not path.exists():
        print("  output/model/predictions.csv missing – model comparison skipped (run 04_model.py first)")
        return None
    pred = pd.read_csv(path, index_col=0)
    pred.index = pd.to_datetime(pred.index, utc=True)
    frame = pd.DataFrame({"y": pred["actual"], "LightGBM": pred["lightgbm"],
                          "LightGBM w/o TSO forecasts": pred["lightgbm_no_tso"]}).join(naive).dropna()
    frame = frame[frame.index >= TEST_START]

    def row(g: pd.DataFrame) -> pd.Series:
        mean_abs = g["y"].abs().mean()
        mae = {c: (g["y"] - g[c]).abs().mean() for c in ["LightGBM", "LightGBM w/o TSO forecasts", *NAIVE]}
        return pd.Series({
            "Hours": len(g), "Avg |price|": mean_abs,
            "nMAE LightGBM_%": 100 * mae["LightGBM"] / mean_abs,
            "nMAE Persistence 24 h_%": 100 * mae["Persistence 24 h"] / mean_abs,
            "nMAE Persistence 168 h_%": 100 * mae[REF] / mean_abs,
            "rMAE LightGBM": mae["LightGBM"] / mae[REF],
            "rMAE w/o TSO forecasts": mae["LightGBM w/o TSO forecasts"] / mae[REF],
            "LightGBM / Persistence 24 h": mae["LightGBM"] / mae["Persistence 24 h"],
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
    ax1.bar(x - 0.2, years["Avg |price|"], 0.4, label="Avg |price| (denominator of the nMAE)", color=COLORS[0])
    ax1.bar(x + 0.2, years["Avg daily spread"], 0.4, label="Avg daily spread (max − min)", color=COLORS[1])
    ax1.set_ylabel("EUR/MWh")
    ax1.set_title("Price level and fluctuation of the CH day-ahead price per year")
    ax1.legend(loc="upper left")

    ref = years[f"nMAE {REF}_%"]
    lo, hi = min(v[0] for v in LITERATURE.values()), max(v[1] for v in LITERATURE.values())
    ax2.plot(x, ref, "s-", label="naive: persistence 168 h (rMAE reference)", color=COLORS[4])
    ax2.plot(x, years["nMAE Persistence 24 h_%"], "o--", lw=1, label="naive: persistence 24 h", color=COLORS[3])
    ax2.fill_between(x, lo * ref, hi * ref, color=COLORS[2], alpha=0.15,
                     label=f"State of the art (rMAE {lo:.2f}–{hi:.2f}, Lago et al. 2021)")
    if model_rmae is not None:
        ax2.plot(x, model_rmae * ref, "o-", color=COLORS[2], label=f"our model, holdout rMAE {model_rmae:.2f} (extrapolated)")
    ax2.axhline(10, color=INK_2, lw=1, ls=":", label="previous target 10 %")
    ax2.set_ylabel("nMAE [%]")
    ax2.set_ylim(0, ref.max() * 1.45)
    ax2.set_title("nMAE per year: naive forecasts and achievable range")
    ax2.set_xticks(x, year_labels(years.index))
    ax2.legend(loc="upper left", ncols=2, fontsize=8)
    fig.savefig(DIR / "01_level_volatility_nmae_by_year.png")
    plt.close(fig)


def plot_months(months: pd.DataFrame) -> float:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 6))
    year = months.index.str[:4].astype(int)
    xcol, ycol = "rel. daily spread_%", f"nMAE {REF}_%"
    cmap = plt.get_cmap("viridis", year.nunique())
    for i, yr in enumerate(sorted(year.unique())):
        sel = months[year == yr]
        ax.scatter(sel[xcol], sel[ycol], s=22, color=cmap(i), label=str(yr))
    r = np.corrcoef(months[xcol], months[ycol])[0, 1]
    slope, icpt = np.polyfit(months[xcol], months[ycol], 1)
    xs = np.linspace(months[xcol].min(), months[xcol].max(), 50)
    ax.plot(xs, slope * xs + icpt, color=INK_2, lw=1, ls="--", label=f"linear fit (r = {r:.2f})")
    ax.axhline(10, color=INK_2, lw=1, ls=":")
    ax.set_xlabel("relative daily spread = avg (max − min) per day / avg |price|  [%]")
    ax.set_ylabel(f"nMAE {REF} [%]")
    ax.set_title("Per month: the more prices fluctuate relative to the level, the larger the nMAE")
    ax.legend(ncols=3, fontsize=8)
    fig.savefig(DIR / "02_nmae_vs_relative_fluctuation_by_month.png")
    plt.close(fig)
    return r


def plot_model(monthly: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True)
    x = np.arange(len(monthly))
    ax1.bar(x - 0.27, monthly["nMAE Persistence 168 h_%"], 0.27, label="Persistence 168 h", color=COLORS[4])
    ax1.bar(x, monthly["nMAE Persistence 24 h_%"], 0.27, label="Persistence 24 h", color=COLORS[3])
    ax1.bar(x + 0.27, monthly["nMAE LightGBM_%"], 0.27, label="LightGBM", color=COLORS[2])
    ax1.axhline(10, color=INK_2, lw=1, ls=":", label="10 %")
    ax1.set_ylabel("nMAE [%]")
    ax1.set_title("Holdout per month: the nMAE fluctuates strongly with the season …")
    ax1.legend(ncols=2)
    lo, hi = min(v[0] for v in LITERATURE.values()), max(v[1] for v in LITERATURE.values())
    ax2.axhspan(lo, hi, color=COLORS[2], alpha=0.12, label=f"State of the art {lo:.2f}–{hi:.2f} (Lago et al. 2021)")
    ax2.plot(x, monthly["rMAE LightGBM"], "o-", color=COLORS[2], label="LightGBM")
    ax2.plot(x, monthly["rMAE w/o TSO forecasts"], "s--", lw=1, color=COLORS[6], label="LightGBM without grid operator forecasts")
    ax2.axhline(1, color=INK_2, lw=1, ls=":")
    ax2.set_ylim(0, max(1.1, monthly["rMAE w/o TSO forecasts"].max() * 1.08))
    ax2.set_ylabel("rMAE (vs. Persistence 168 h)")
    ax2.set_title("… the rMAE (model relative to the naive forecast) remains much more stable")
    ax2.set_xticks(x, monthly.index, rotation=45)
    ax2.legend(loc="upper left", fontsize=8)
    fig.savefig(DIR / "03_holdout_nmae_rmae_by_month.png")
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
    years.to_csv(DIR / "metrics_by_year.csv", float_format="%.3f")
    months.to_csv(DIR / "metrics_by_month.csv", float_format="%.3f")

    comparison = model_comparison(naive)
    model_rmae = None
    if comparison:
        monthly, overall = comparison
        monthly.to_csv(DIR / "holdout_by_month.csv", float_format="%.3f")
        model_rmae = overall["rMAE LightGBM"]
        plot_model(monthly)

    plot_years(years, model_rmae)
    r_month = plot_months(months)

    ref = years[f"nMAE {REF}_%"]
    scen = pd.DataFrame({f"rMAE {r}": r * ref for r in RMAE_SCENARIOS})
    if model_rmae is not None:
        scen.insert(0, f"our model (rMAE {model_rmae:.2f})", model_rmae * ref)
    scen.insert(0, "naive (persistence 168 h)", ref)
    scen.loc["Avg years"] = scen.mean()
    scen.to_csv(DIR / "scenarios_nmae_by_year.csv", float_format="%.2f")

    show = years[["Hours", "Resolution_min", "Mean", "Avg |price|", "Std", "Avg daily spread",
                  "rel. daily spread_%", "Neg. hours", "nMAE Persistence 24 h_%", "nMAE Persistence 168 h_%",
                  "nMAE Weekly naive_%", "nMAE Profile 14 days_%"]].copy()
    show.index = year_labels(show.index)
    lit = pd.DataFrame(LITERATURE, index=["rMAE min (DNN ensemble)", "rMAE max (weakest LEAR)"]).T
    lines = [
        "# Target value analysis: which nMAE is sensible for the 24h forecast?",
        "",
        f"CH-Day-Ahead (energy-charts), {frame.index.min().tz_convert(TZ):%d.%m.%Y} – "
        f"{frame.index.max().tz_convert(TZ):%d.%m.%Y}, {len(frame)} hours, hourly averaged. "
        f"\\* = current year (incomplete).",
        "",
        "- nMAE = MAE / avg |price|",
        f"- rMAE = MAE_model / MAE_naive with naive = {REF} (p_d−7,h), definition according to Lago et al. (2021), eq. 9",
        "",
        "## Level, fluctuation and difficulty per year",
        "",
        show.to_markdown(floatfmt=".1f"),
        "",
        "Entire period: nMAE " + ", ".join(f"{n} {total[f'nMAE {n}_%']:.1f} %" for n in NAIVE) + ".",
        "",
        f"Per month, the nMAE of {REF} correlates with the relative daily spread with r = {r_month:.2f} "
        f"(range of the months: {months[f'nMAE {REF}_%'].min():.1f} – {months[f'nMAE {REF}_%'].max():.1f} %).",
        "",
        "![01](01_level_volatility_nmae_by_year.png)",
        "",
        "![02](02_nmae_vs_relative_fluctuation_by_month.png)",
        "",
        "## Literature: rMAE of state-of-the-art models",
        "",
        "Lago, Marcjasz, De Schutter & Weron (2021), Applied Energy 293, 116983, Tab. 2 and 3 "
        "(2 years of testing each between 2015 and 2018):",
        "",
        lit.to_markdown(floatfmt=".3f"),
        "",
    ]
    if comparison:
        lines += [
            "## Our model in the holdout: nMAE vs. rMAE",
            "",
            f"Overall: nMAE LightGBM {overall['nMAE LightGBM_%']:.1f} %, {REF} {overall['nMAE Persistence 168 h_%']:.1f} % "
            f"→ **rMAE {overall['rMAE LightGBM']:.2f}** (without grid operator forecasts {overall['rMAE w/o TSO forecasts']:.2f}; "
            f"relative to persistence 24 h {overall['LightGBM / Persistence 24 h']:.2f}).",
            "",
            f"Dispersion per month: nMAE LightGBM {monthly['nMAE LightGBM_%'].min():.1f} – "
            f"{monthly['nMAE LightGBM_%'].max():.1f} % (factor "
            f"{monthly['nMAE LightGBM_%'].max() / monthly['nMAE LightGBM_%'].min():.1f}), rMAE "
            f"{monthly['rMAE LightGBM'].min():.2f} – {monthly['rMAE LightGBM'].max():.2f}.",
            "",
            monthly.to_markdown(floatfmt=".2f"),
            "",
            "![03](03_holdout_nmae_rmae_by_month.png)",
            "",
        ]
    lines += [
        "## Scenarios: achievable nMAE per year for a given rMAE",
        "",
        f"nMAE ≈ rMAE × nMAE({REF}) of the respective year.",
        "",
        scen.rename(index=lambda i: f"{i}*" if str(i) == str(NOW.year) else str(i)).to_markdown(floatfmt=".1f"),
        "",
    ]
    (DIR / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
