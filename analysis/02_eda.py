"""Schritt 02 – Explorative Datenanalyse: Was treibt den Schweizer Day-Ahead-Preis?

Erzeugt Grafiken unter output/eda/ und eine Zusammenfassung output/eda/summary.md.
Leitfrage: Wie stark hängt der CH-Preis an der europäischen (v. a. deutschen) Residuallast,
an Wind/Solar, am Import/Export und am Tages-/Saisonverlauf?

Aufruf:  .venv/bin/python 02_eda.py
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import COLORS, INK_2, TZ, out_dir, plot_style, processed_hourly

OUT = out_dir("eda")
SEASONS = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Frühling", 4: "Frühling", 5: "Frühling",
           6: "Sommer", 7: "Sommer", 8: "Sommer", 9: "Herbst", 10: "Herbst", 11: "Herbst"}
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
    # Residuallast DE+CH (Last minus Wind und Solar) – der zentrale Preistreiber
    if {"act_de_residual_load", "act_ch_residual_load"} <= set(df):
        df["resid_dech"] = df["act_de_residual_load"] + df["act_ch_residual_load"]
    df["wind_solar_de"] = df.filter(regex=r"^act_de_(wind_onshore|wind_offshore|solar)$").sum(axis=1, min_count=1)
    for other in ("de", "fr", "it", "at"):
        if f"price_{other}" in df:
            df[f"spread_ch_{other}"] = df["price_ch"] - df[f"price_{other}"]
    return df


def prices_over_time(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11, 4))
    names = {"ch": "Schweiz", "de": "Deutschland-Lux.", "fr": "Frankreich", "it": "Italien Nord"}
    for i, (key, label) in enumerate(names.items()):
        if f"price_{key}" in df:
            weekly = df[f"price_{key}"].resample("W").mean()
            ax.plot(weekly.index, weekly, color=COLORS[i], label=label, linewidth=1.8 if key == "ch" else 1.2)
    ax.set_title("Day-Ahead-Preise, Wochenmittel")
    ax.set_ylabel("EUR/MWh")
    ax.legend(loc="upper right", ncols=4)
    save(fig, "01_preise_zeitverlauf.png")


def correlations(df: pd.DataFrame) -> pd.DataFrame:
    drivers = {
        "price_de": "Preis DE", "price_it": "Preis IT-Nord", "price_fr": "Preis FR", "price_at": "Preis AT",
        "resid_dech": "Residuallast DE+CH", "act_de_residual_load": "Residuallast DE",
        "act_ch_residual_load": "Residuallast CH", "act_de_load": "Last DE", "act_ch_load": "Last CH",
        "wind_solar_de": "Wind+Solar DE", "act_de_solar": "Solar DE", "act_de_wind_onshore": "Wind onshore DE",
        "act_ch_solar": "Solar CH", "act_ch_hydro_water_reservoir": "Speicherkraft CH",
        "act_ch_hydro_run_of_river": "Laufwasser CH", "act_ch_nuclear": "Kernkraft CH",
        "act_de_fossil_gas": "Gas DE", "act_de_fossil_hard_coal": "Steinkohle DE",
        "act_ch_renewable_share_of_load": "EE-Anteil Last CH", "act_de_renewable_share_of_load": "EE-Anteil Last DE",
        "cbet_ch_sum": "Nettoimport CH", "wx_zurich_temperature_2m": "Temperatur Zürich (Prognose)",
    }
    rows = []
    for col, label in drivers.items():
        if col in df:
            pair = df[["price_ch", col]].dropna()
            rows.append({"Treiber": label, "Pearson r": pair.corr().iloc[0, 1],
                         "Spearman ρ": pair.corr(method="spearman").iloc[0, 1], "n": len(pair)})
    table = pd.DataFrame(rows).sort_values("Spearman ρ", key=np.abs, ascending=False)
    table.to_csv(OUT / "korrelationen.csv", index=False, float_format="%.3f")

    fig, ax = plt.subplots(figsize=(8, 0.32 * len(table) + 1))
    t = table.iloc[::-1]
    ax.barh(t["Treiber"], t["Spearman ρ"], color=[COLORS[0] if v >= 0 else COLORS[1] for v in t["Spearman ρ"]],
            height=0.65)
    ax.axvline(0, color=INK_2, linewidth=0.8)
    ax.set_xlim(-1, 1)
    ax.set_title("Rangkorrelation (Spearman) mit dem CH-Day-Ahead-Preis")
    ax.set_xlabel("ρ  (blau = Preis steigt mit Treiber, orange = Preis sinkt)")
    save(fig, "02_korrelationen.png")
    return table


def residual_load_scatter(df: pd.DataFrame) -> None:
    cols = [c for c in ("act_de_residual_load", "act_ch_residual_load") if c in df]
    fig, axes = plt.subplots(1, len(cols), figsize=(5.5 * len(cols), 4.5), squeeze=False)
    for ax, col, color in zip(axes[0], cols, COLORS):
        pair = df[[col, "price_ch"]].dropna()
        ax.scatter(pair[col] / 1000, pair["price_ch"], s=3, alpha=0.12, color=color, linewidths=0)
        # Binned Median als Trendlinie
        bins = pd.qcut(pair[col], 30, duplicates="drop")
        med = pair.groupby(bins, observed=True).median()
        ax.plot(med[col] / 1000, med["price_ch"], color="#0b0b0b", linewidth=2, label="Median je Klasse")
        r = pair.corr(method="spearman").iloc[0, 1]
        land = "DE" if "_de_" in col else "CH"
        ax.set_title(f"Residuallast {land} vs. CH-Preis (ρ = {r:.2f})", fontsize=11)
        ax.set_xlabel("Residuallast = Last − Wind − Solar [GW]")
        ax.set_ylabel("CH Day-Ahead [EUR/MWh]")
        ax.set_ylim(np.percentile(pair["price_ch"], 0.2), np.percentile(pair["price_ch"], 99.8))
        ax.legend(loc="upper left")
    save(fig, "03_residuallast_vs_preis.png")


def daily_profiles(df: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for ax, weekend, title in zip(axes, (False, True), ("Werktag", "Wochenende")):
        sub = df[df["weekend"] == weekend]
        for i, season in enumerate(["Winter", "Frühling", "Sommer", "Herbst"]):
            prof = sub[sub["season"] == season].groupby("hour")["price_ch"].mean()
            ax.plot(prof.index, prof, color=COLORS[i], label=season)
        ax.set_title(f"CH-Tagesprofil – {title}")
        ax.set_xlabel("Stunde (Lokalzeit)")
        ax.set_xticks(range(0, 24, 3))
    axes[0].set_ylabel("Mittlerer Preis [EUR/MWh]")
    axes[1].legend(loc="lower left")
    save(fig, "04_tagesprofile.png")


def heatmap(df: pd.DataFrame) -> None:
    pivot = df.pivot_table(index="hour", columns="month", values="price_ch", aggfunc="mean")
    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(pivot, aspect="auto", cmap="Blues", origin="lower")
    ax.set_xticks(range(12), ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"])
    ax.set_yticks(range(0, 24, 3))
    ax.set_xlabel("Monat")
    ax.set_ylabel("Stunde (Lokalzeit)")
    ax.grid(False)
    ax.set_title("CH-Preis nach Stunde × Monat (Mittel 2024–2026)")
    fig.colorbar(im, ax=ax, label="EUR/MWh")
    save(fig, "05_heatmap_stunde_monat.png")


def solar_effect(df: pd.DataFrame) -> None:
    """Mittagspreis vs. deutsche Solar-Einspeisung – der 'Solar-Knick'."""
    if "act_de_solar" not in df:
        return
    noon = df[df["hour"].between(11, 15)][["act_de_solar", "price_ch", "price_de"]].dropna()
    bins = pd.cut(noon["act_de_solar"] / 1000, np.arange(0, noon["act_de_solar"].max() / 1000 + 5, 5))
    g = noon.groupby(bins, observed=True)[["price_ch", "price_de"]].median()
    centers = [b.mid for b in g.index]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(centers, g["price_ch"], marker="o", markersize=5, color=COLORS[0], label="Schweiz")
    ax.plot(centers, g["price_de"], marker="o", markersize=5, color=COLORS[1], label="Deutschland")
    ax.axhline(0, color=INK_2, linewidth=0.8)
    ax.set_title("Mittagspreis (11–15 Uhr) vs. Solar-Einspeisung Deutschland")
    ax.set_xlabel("Solar DE [GW]")
    ax.set_ylabel("Median-Preis [EUR/MWh]")
    ax.legend()
    save(fig, "06_solar_de_vs_mittagspreis.png")


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
    ax.set_title("CH-Preis nach erneuerbarem Anteil an der Last (Schweiz)")
    ax.set_xlabel("Erneuerbarer Anteil an der Last [%]")
    ax.set_ylabel("Median-Preis [EUR/MWh]")
    save(fig, "07_ee_anteil_vs_preis.png")


def imports(df: pd.DataFrame) -> None:
    cols = {c: c.removeprefix("cbet_ch_").capitalize() for c in df if c.startswith("cbet_ch_") and c != "cbet_ch_sum"}
    if not cols:
        return
    labels = {"Germany": "Deutschland", "France": "Frankreich", "Italy": "Italien", "Austria": "Österreich"}
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for ax, season in zip(axes, ("Winter", "Sommer")):
        sub = df[df["season"] == season]
        for i, (col, name) in enumerate(cols.items()):
            prof = sub.groupby("hour")[col].mean()
            ax.plot(prof.index, prof, color=COLORS[i], label=labels.get(name, name))
        ax.axhline(0, color=INK_2, linewidth=0.8)
        ax.set_title(f"CH-Austausch je Nachbar – {season}")
        ax.set_xlabel("Stunde (Lokalzeit)")
        ax.set_xticks(range(0, 24, 3))
    axes[0].set_ylabel("GW  (+ Import nach CH, − Export)")
    handles, labels_ = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels_, loc="lower center", ncols=4, bbox_to_anchor=(0.5, -0.06))
    save(fig, "08_import_export_profil.png")


def spreads(df: pd.DataFrame) -> None:
    cols = [c for c in ("spread_ch_de", "spread_ch_fr", "spread_ch_it") if c in df]
    monthly = df[cols].resample("MS").mean()
    fig, ax = plt.subplots(figsize=(11, 4))
    names = {"spread_ch_de": "CH − DE", "spread_ch_fr": "CH − FR", "spread_ch_it": "CH − IT-Nord"}
    for i, c in enumerate(cols):
        ax.plot(monthly.index, monthly[c], color=COLORS[i], marker="o", markersize=4, label=names[c])
    ax.axhline(0, color=INK_2, linewidth=0.8)
    ax.set_title("Preisdifferenz Schweiz zu Nachbarn, Monatsmittel")
    ax.set_ylabel("EUR/MWh")
    ax.legend(ncols=3)
    save(fig, "09_spreads.png")


def summary_stats(df: pd.DataFrame) -> None:
    note("\n## Kennzahlen je Jahr (CH Day-Ahead, EUR/MWh)\n")
    g = df.groupby("year")["price_ch"]
    stats = pd.DataFrame({"Mittel": g.mean(), "Median": g.median(), "Std": g.std(),
                          "Min": g.min(), "Max": g.max(), "Std. negativ": g.apply(lambda s: (s < 0).sum()),
                          "Stunden": g.size()})
    if "price_de" in df:
        stats["Std. negativ DE"] = df.groupby("year")["price_de"].apply(lambda s: (s < 0).sum())
    note(stats.round(1).to_markdown())
    daily_spread = df["price_ch"].resample("D").agg(lambda s: s.max() - s.min())
    note(f"\nMittlere Tagesspanne (max − min) CH: {daily_spread.mean():.1f} EUR/MWh")


def main() -> None:
    plot_style()
    df = load()
    note("# EDA – Preistreiber des Schweizer Day-Ahead-Preises\n")
    note(f"Zeitraum: {df.index.min():%Y-%m-%d} bis {df.index.max():%Y-%m-%d}, {len(df)} Stunden.\n")
    summary_stats(df)
    note("\n## Grafiken\n")
    prices_over_time(df)
    table = correlations(df)
    residual_load_scatter(df)
    daily_profiles(df)
    heatmap(df)
    solar_effect(df)
    renewable_share(df)
    imports(df)
    spreads(df)
    note("\n## Korrelation der Treiber mit dem CH-Preis\n")
    note(table.to_markdown(index=False, floatfmt=".2f"))
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
