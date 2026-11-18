"""Step 02 – exploratory data analysis: what drives the Swiss day-ahead price?

Creates charts under output/eda/ and a summary output/eda/summary.md.
Guiding question: how strongly does the CH price depend on the European (mainly German) residual load,
on wind/solar, on import/export and on the daily/seasonal pattern?

Usage:  .venv/bin/python 02_eda.py
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import COLORS, INK_2, TZ, out_dir, plot_style, processed_hourly

OUT = out_dir("eda")
SEASONS = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
           6: "Summer", 7: "Summer", 8: "Summer", 9: "Autumn", 10: "Autumn", 11: "Autumn"}
lines: list[str] = []


def note(text: str = "") -> None:
    print(text)
    lines.append(text)


def save(fig, name: str) -> None:
    fig.savefig(OUT / name)
    plt.close(fig)
    note(f"![{name}]({name})")


def load() -> pd.DataFrame:
    df = processed_hourly()
    df = df[df["price_ch"].notna()].copy()
    local = df.index.tz_convert(TZ)
    df["hour"], df["month"], df["year"] = local.hour, local.month, local.year
    df["weekend"] = local.day_of_week >= 5
    df["season"] = df["month"].map(SEASONS)
    # Residual load DE+CH (load minus wind and solar) – the central price driver
    if {"act_de_residual_load", "act_ch_residual_load"} <= set(df):
        df["resid_dech"] = df["act_de_residual_load"] + df["act_ch_residual_load"]
    df["wind_solar_de"] = df.filter(regex=r"^act_de_(wind_onshore|wind_offshore|solar)$").sum(axis=1, min_count=1)
    for other in ("de", "fr", "it", "at"):
        if f"price_{other}" in df:
            df[f"spread_ch_{other}"] = df["price_ch"] - df[f"price_{other}"]
    return df


def prices_over_time(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11, 4))
    names = {"ch": "Switzerland", "de": "Germany-Lux.", "fr": "France", "it": "Italy North"}
    for i, (key, label) in enumerate(names.items()):
        if f"price_{key}" in df:
            weekly = df[f"price_{key}"].resample("W").mean()
            ax.plot(weekly.index, weekly, color=COLORS[i], label=label, linewidth=1.8 if key == "ch" else 1.2)
    ax.set_title("Day-ahead prices, weekly mean")
    ax.set_ylabel("EUR/MWh")
    ax.legend(loc="upper right", ncols=4)
    save(fig, "01_prices_over_time.png")


def correlations(df: pd.DataFrame) -> pd.DataFrame:
    drivers = {
        "price_de": "Price DE", "price_it": "Price IT-North", "price_fr": "Price FR", "price_at": "Price AT",
        "resid_dech": "Residual load DE+CH", "act_de_residual_load": "Residual load DE",
        "act_ch_residual_load": "Residual load CH", "act_de_load": "Load DE", "act_ch_load": "Load CH",
        "wind_solar_de": "Wind+solar DE", "act_de_solar": "Solar DE", "act_de_wind_onshore": "Wind onshore DE",
        "act_ch_solar": "Solar CH", "act_ch_hydro_water_reservoir": "Reservoir hydro CH",
        "act_ch_hydro_run_of_river": "Run-of-river CH", "act_ch_nuclear": "Nuclear CH",
        "act_de_fossil_gas": "Gas DE", "act_de_fossil_hard_coal": "Hard coal DE",
        "act_ch_renewable_share_of_load": "RE share of load CH", "act_de_renewable_share_of_load": "RE share of load DE",
        "cbet_ch_sum": "Net import CH", "wx_zurich_temperature_2m": "Temperature Zurich (forecast)",
    }
    rows = []
    for col, label in drivers.items():
        if col in df:
            pair = df[["price_ch", col]].dropna()
            rows.append({"Driver": label, "Pearson r": pair.corr().iloc[0, 1],
                         "Spearman ρ": pair.corr(method="spearman").iloc[0, 1], "n": len(pair)})
    table = pd.DataFrame(rows).sort_values("Spearman ρ", key=np.abs, ascending=False)
    table.to_csv(OUT / "correlations.csv", index=False, float_format="%.3f")

    fig, ax = plt.subplots(figsize=(8, 0.32 * len(table) + 1))
    t = table.iloc[::-1]
    ax.barh(t["Driver"], t["Spearman ρ"], color=[COLORS[0] if v >= 0 else COLORS[1] for v in t["Spearman ρ"]],
            height=0.65)
    ax.axvline(0, color=INK_2, linewidth=0.8)
    ax.set_xlim(-1, 1)
    ax.set_title("Rank correlation (Spearman) with the CH day-ahead price")
    ax.set_xlabel("ρ  (blue = price rises with driver, orange = price falls)")
    save(fig, "02_correlations.png")
    return table


def residual_load_scatter(df: pd.DataFrame) -> None:
    cols = [c for c in ("act_de_residual_load", "act_ch_residual_load") if c in df]
    fig, axes = plt.subplots(1, len(cols), figsize=(5.5 * len(cols), 4.5), squeeze=False)
    for ax, col, color in zip(axes[0], cols, COLORS):
        pair = df[[col, "price_ch"]].dropna()
        ax.scatter(pair[col] / 1000, pair["price_ch"], s=3, alpha=0.12, color=color, linewidths=0)
        # Binned median as trend line
        bins = pd.qcut(pair[col], 30, duplicates="drop")
        med = pair.groupby(bins, observed=True).median()
        ax.plot(med[col] / 1000, med["price_ch"], color="#0b0b0b", linewidth=2, label="Median per bin")
        r = pair.corr(method="spearman").iloc[0, 1]
        country = "DE" if "_de_" in col else "CH"
        ax.set_title(f"Residual load {country} vs. CH price (ρ = {r:.2f})", fontsize=11)
        ax.set_xlabel("Residual load = load − wind − solar [GW]")
        ax.set_ylabel("CH Day-Ahead [EUR/MWh]")
        ax.set_ylim(np.percentile(pair["price_ch"], 0.2), np.percentile(pair["price_ch"], 99.8))
        ax.legend(loc="upper left")
    save(fig, "03_residual_load_vs_price.png")


def daily_profiles(df: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for ax, weekend, title in zip(axes, (False, True), ("Weekday", "Weekend")):
        sub = df[df["weekend"] == weekend]
        for i, season in enumerate(["Winter", "Spring", "Summer", "Autumn"]):
            prof = sub[sub["season"] == season].groupby("hour")["price_ch"].mean()
            ax.plot(prof.index, prof, color=COLORS[i], label=season)
        ax.set_title(f"CH daily profile – {title}")
        ax.set_xlabel("Hour (local time)")
        ax.set_xticks(range(0, 24, 3))
    axes[0].set_ylabel("Mean price [EUR/MWh]")
    axes[1].legend(loc="lower left")
    save(fig, "04_daily_profiles.png")


def heatmap(df: pd.DataFrame) -> None:
    pivot = df.pivot_table(index="hour", columns="month", values="price_ch", aggfunc="mean")
    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(pivot, aspect="auto", cmap="Blues", origin="lower")
    ax.set_xticks(range(12), ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_yticks(range(0, 24, 3))
    ax.set_xlabel("Month")
    ax.set_ylabel("Hour (local time)")
    ax.grid(False)
    ax.set_title("CH price by hour × month (mean 2024–2026)")
    fig.colorbar(im, ax=ax, label="EUR/MWh")
    save(fig, "05_heatmap_hour_month.png")


def solar_effect(df: pd.DataFrame) -> None:
    """Midday price vs. German solar feed-in – the 'solar dip'."""
    if "act_de_solar" not in df:
        return
    noon = df[df["hour"].between(11, 15)][["act_de_solar", "price_ch", "price_de"]].dropna()
    bins = pd.cut(noon["act_de_solar"] / 1000, np.arange(0, noon["act_de_solar"].max() / 1000 + 5, 5))
    g = noon.groupby(bins, observed=True)[["price_ch", "price_de"]].median()
    centers = [b.mid for b in g.index]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(centers, g["price_ch"], marker="o", markersize=5, color=COLORS[0], label="Switzerland")
    ax.plot(centers, g["price_de"], marker="o", markersize=5, color=COLORS[1], label="Germany")
    ax.axhline(0, color=INK_2, linewidth=0.8)
    ax.set_title("Midday price (11:00–15:00) vs. solar feed-in Germany")
    ax.set_xlabel("Solar DE [GW]")
    ax.set_ylabel("Median price [EUR/MWh]")
    ax.legend()
    save(fig, "06_solar_de_vs_midday_price.png")


def renewable_share(df: pd.DataFrame) -> None:
    col = "act_ch_renewable_share_of_load"
    if col not in df:
        return
    pair = df[[col, "price_ch"]].dropna()
    bins = pd.cut(pair[col], np.arange(0, 105, 10))
    g = pair.groupby(bins, observed=True)["price_ch"].agg(["median", "count"])
    g = g[g["count"] > 50]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar([str(int(b.left)) + "–" + str(int(b.right)) for b in g.index], g["median"], color=COLORS[2], width=0.7)
    ax.set_title("CH price by renewable share of load (Switzerland)")
    ax.set_xlabel("Renewable share of load [%]")
    ax.set_ylabel("Median price [EUR/MWh]")
    save(fig, "07_re_share_vs_price.png")


def imports(df: pd.DataFrame) -> None:
    cols = {c: c.removeprefix("cbet_ch_").capitalize() for c in df if c.startswith("cbet_ch_") and c != "cbet_ch_sum"}
    if not cols:
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for ax, season in zip(axes, ("Winter", "Summer")):
        sub = df[df["season"] == season]
        for i, (col, name) in enumerate(cols.items()):
            prof = sub.groupby("hour")[col].mean()
            ax.plot(prof.index, prof, color=COLORS[i], label=name)
        ax.axhline(0, color=INK_2, linewidth=0.8)
        ax.set_title(f"CH exchange per neighbour – {season}")
        ax.set_xlabel("Hour (local time)")
        ax.set_xticks(range(0, 24, 3))
    axes[0].set_ylabel("GW  (+ import to CH, − export)")
    handles, labels_ = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels_, loc="lower center", ncols=4, bbox_to_anchor=(0.5, -0.06))
    save(fig, "08_import_export_profile.png")


def spreads(df: pd.DataFrame) -> None:
    cols = [c for c in ("spread_ch_de", "spread_ch_fr", "spread_ch_it") if c in df]
    monthly = df[cols].resample("MS").mean()
    fig, ax = plt.subplots(figsize=(11, 4))
    names = {"spread_ch_de": "CH − DE", "spread_ch_fr": "CH − FR", "spread_ch_it": "CH − IT-North"}
    for i, c in enumerate(cols):
        ax.plot(monthly.index, monthly[c], color=COLORS[i], marker="o", markersize=4, label=names[c])
    ax.axhline(0, color=INK_2, linewidth=0.8)
    ax.set_title("Price difference Switzerland vs. neighbours, monthly mean")
    ax.set_ylabel("EUR/MWh")
    ax.legend(ncols=3)
    save(fig, "09_spreads.png")


def summary_stats(df: pd.DataFrame) -> None:
    note("\n## Statistics per year (CH day-ahead, EUR/MWh)\n")
    g = df.groupby("year")["price_ch"]
    stats = pd.DataFrame({"Mean": g.mean(), "Median": g.median(), "Std": g.std(),
                          "Min": g.min(), "Max": g.max(), "Neg. hours": g.apply(lambda s: (s < 0).sum()),
                          "Hours": g.size()})
    if "price_de" in df:
        stats["Neg. hours DE"] = df.groupby("year")["price_de"].apply(lambda s: (s < 0).sum())
    note(stats.round(1).to_markdown())
    daily_spread = df["price_ch"].resample("D").agg(lambda s: s.max() - s.min())
    note(f"\nMean daily spread (max − min) CH: {daily_spread.mean():.1f} EUR/MWh")


def main() -> None:
    plot_style()
    df = load()
    note("# EDA – price drivers of the Swiss day-ahead price\n")
    note(f"Period: {df.index.min():%Y-%m-%d} to {df.index.max():%Y-%m-%d}, {len(df)} hours.\n")
    summary_stats(df)
    note("\n## Charts\n")
    prices_over_time(df)
    table = correlations(df)
    residual_load_scatter(df)
    daily_profiles(df)
    heatmap(df)
    solar_effect(df)
    renewable_share(df)
    imports(df)
    spreads(df)
    note("\n## Correlation of the drivers with the CH price\n")
    note(table.to_markdown(index=False, floatfmt=".2f"))
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
