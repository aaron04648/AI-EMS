# Project Report

*The project will be structured using the CRISP-DM framework. Details about the framework can be found [here](https://www.datascience-pm.com/crisp-dm-2/). We will not conduct the deployment step. This page will serve as your project report. The current content provides a rough structure and helps you keep track of the deadlines for each deliverable. The first- and second-level headings should remain as provided in the template. All other content can be freely edited and adapted to your project.*

## Project Details

> 
### Team Members

| Name | Major task |
|--------|------------|
| Aaron Stupf |  Rechner   |
| Sebastian Lischer |  Analyse |



### Project Name

> Intelligent EMS
> 

### Pitch
Dynamic electricity tariffs make the timing of household consumption increasingly important. Swiss **day-ahead prices are fixed at auction on the previous day**: after publication, the next delivery day's prices are already available to everyone. Our primary focus is to explain the market expectations behind these prices and investigate an extended **48h planning profile: 24h fixed prices + 24h predicted prices**. An optional household PV and battery calculator will measure whether the additional forecast day improves scheduling compared with using published prices alone. Savings depend on the tariff, consumption, battery constraints and forecast accuracy; they are not guaranteed by this EDA.

---

# 1. Business Understanding

## Problem Statement

Households need to understand dynamic electricity prices and schedule flexible consumption, PV and batteries accordingly. However, the prices in our dataset are **day-ahead auction prices, not real-time prices**. The next delivery day's hourly prices are fixed on the previous day and publicly available after the auction. An EMS can therefore optimize that known period without forecasting it.

Our analysis is effectively **reverse-engineering day-ahead price formation**: market participants bid on their expectations of next-day demand, sunshine, wind and supply. Low prices during hours with high subsequently measured solar generation are consistent with anticipated solar supply; they do not mean that simultaneous production changed an already fixed price. We do not observe participants' forecasts or a single central pricing model, and observational correlations cannot identify causality.

The potential forecasting value lies **beyond the published delivery day**, for example in a 48h profile consisting of 24h known and 24h predicted prices. This reduces the optimization headroom attributable to our model: it must add value over an EMS that already uses public prices, rather than claim all load-shifting savings. The 24h/48h illustration refers to aligned delivery days; the rolling known horizon depends on the time of publication, and daylight-saving delivery days contain 23 or 25 hours.

## Goals

### 1. Core Market Analysis & Extended Price Profile:
By the end of the semester, explain Swiss day-ahead price patterns using European market data and evaluate a **48h delivery-day profile (24h published + 24h predicted)**. At an issuance time after the auction on day D, D+1 is known and D+2 is the forecast target. Only information available at that issuance time may be used, including appropriately archived **D+2** solar, wind and load forecasts. D+1 forecasts and delayed Pronovo measurements are not substitutes.

The original target **nMAE < 10%** (MAE / mean absolute price), with rMAE against same-hour-last-week persistence and a chronological holdout of at least 12 months, remains an **aspirational target for the newly defined second-day task**, not a result established by this EDA. The volatility analysis in [analysis/06_zielwert.py](analysis/06_zielwert.py) motivates the metric, but neither its threshold nor the existing pre-auction next-day model results validate D+2 performance. Report errors for the predicted block separately from the known block; including fixed prices would artificially improve accuracy. Compare 24h, 48h and 168h persistence using only published prices, and reproduce the actual issuance schedule in evaluation.

### 2. Optional Predictive Use-Case & Impact Calculator:
Within the same timeframe, simulate household PV and battery schedules and compare **published-price-only planning** with the extended profile under identical consumption, tariff and battery assumptions. Report incremental net savings after efficiency losses, capacity and power limits, and forecast errors. Wholesale EUR/MWh spreads are not household CHF savings; claims of 0.15 CHF per interval or 500 CHF annually require a separate, validated scenario. The calculator illustrates decisions rather than proving that transparency causes solar adoption.

### 3. Documentation & Expert Validation:
We will finalize a comprehensive technical report and interactive demonstration by project completion, validating the usability and practical viability of our system through a dedicated presentation and review with the company Plan-E after the final discussion.

# 2. Data Understanding

## Data Sources

| Data source | Description |
|-------------|-------------|
| [Energy-Charts – Stromproduktion Schweiz (Pronovo)](https://www.energy-charts.info/charts/power/chart.htm?l=de&c=CH&source=pronovo&interval=year) | Fraunhofer ISE. Net electricity generation in Switzerland by source (hydro by category, nuclear, PV, wind, biomass, fossil), pumped-storage consumption, load and cross-border trade; 15-minute resolution in MW from 2020. Underlying sources according to Energy-Charts: Pronovo, ENTSO-E, SFOE (energetically corrected, published with a one-month delay). License CC BY 4.0. |
| [Energy-Charts – Börsenstrompreise und Handelsvolumen Schweiz](https://www.energy-charts.info/charts/price_volume/chart.htm?l=de&c=CH&interval=year) | Fraunhofer ISE. Swiss day-ahead auction: price (EUR/MWh) and traded volume (MWh) from 2014, hourly (also after the EU switch to 15-minute products on 01.10.2025). Underlying sources according to Energy-Charts: ENTSO-E, EPEX SPOT. License CC BY 4.0. |


## Exploratory Data Analysis

- Notebook to run the EDA: [01_data_understanding.ipynb](notebooks/01_data_understanding.ipynb)

The notebook loads the data behind the two Energy-Charts views, caches the annual JSON files in `data/raw/energy_charts_web/` (not versioned), and exports the PNGs below at **180 dpi**. The report cutoff remains **30.10.2026**, so the rerun does not silently extend the observation period. The source was retrieved again on **09.11.2026**; the production series still ends on **31.08.2026**. [Computed summary statistics and cache checksums](figures/eda/summary_statistics.json) record the exact input basis and environment.

Run from the repository root with Python 3.13:

```powershell
python -m pip install -r notebooks\requirements.txt
python -m nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=1800 notebooks\01_data_understanding.ipynb
```

Alternatively, open the notebook and run all cells in order. The first run requires internet access; subsequent runs use the same local cache, including 2026. To deliberately refresh inputs, remove only the relevant annual cache files and rerun; historical revisions can change results, so compare the exported SHA-256 checksums. The separate `analysis/` model pipeline is not required for this EDA.

### Data Quality

#### Coverage, missing values and schema changes

| Check | Day-ahead price / volume | Pronovo production |
|---|---|---|
| Local-time coverage | 01.01.2015–30.10.2026 | 01.01.2020–31.08.2026 |
| Observations / UTC timestamp gaps | 103'704 / **0** | 233'756 / **0** |
| Resolution | **Hourly**, including after 01.10.2025 | 15-minute grid; load is hourly repeated four times, trade mostly hourly |
| Missing values | **0 prices**, **12 volume values** | **0 PV, wind or load values**; 6 missing trade values; many structural gaps in renamed/introduced categories |
| Types and uniqueness | Numeric columns; unique, sorted UTC index | Numeric columns; unique, sorted UTC index |
| Availability | Fixed at the previous day's auction | Source metadata states a one-month publication delay; this snapshot has a longer gap to the report cutoff |

**Coverage and missing-value chart.** The top panel shows the proportion of non-missing values per production series and year, not the share of a complete calendar year. Nuclear and pumped-storage consumption only become available on 01.10.2022; hydro is aggregated differently in 2020, and biomass/fossil categories change names. The bottom panel shows one missing volume value each year, at 03:00 local time on the spring daylight-saving transition. Missing categories are not zero production. Comparable generation analysis starts on **01.10.2022**, with whole-year comparisons from 2023.

![Coverage and missing values: Pronovo schema by year and missing price/volume counts](figures/eda/00_datenqualitaet.png)

#### Full time series and changes in price level

**Swiss day-ahead prices, 2015–30.10.2026.** Daily means and a centered 30-day mean reveal structural changes rather than timestamp gaps. Annual mean prices ranged from **34.00 to 52.22 EUR/MWh in 2015–2020**, reached **281.65 in 2022**, and are **131.31 in Jan–Oct 2026** (partial year). The crisis year contains **3'132 hours above 300 EUR/MWh**. These regimes challenge stationarity; evaluate models chronologically across seasons and price levels. The centered smoother is descriptive, not an available forecasting feature.

![Full Swiss day-ahead price series: daily means and 30-day mean](figures/eda/01_preis_zeitverlauf.png)

#### Outliers and price distribution

**Hourly price distribution, last 24 months.** All **17'521 observations** are shown without clipping, including **542 negative-price hours**. The full-series extremes are **−463.68 EUR/MWh (2026)** and **871.61 EUR/MWh (2022)**; the histogram window has a maximum of **596.70 EUR/MWh**. Extreme prices are economically plausible market outcomes and are retained, not automatically treated as measurement errors. The notebook also counts annual values outside P25 − 3 × IQR / P75 + 3 × IQR for transparent screening. This is not independent verification of every observation.

![Unclipped hourly Swiss price histogram for the last 24 months](figures/eda/02_preis_verteilung.png)

**Processing and limitations.** The unrelated IT-North-CH auction is excluded. Prices and generation are joined on UTC hours; MW production is averaged to hourly power, while energy totals integrate the original quarter-hour observations (MW × 0.25h). Aliases are grouped only after examining schema coverage. Missing volume remains missing during aggregation. We do not interpolate the price series, drop negative prices, or invent quarter-hour price variation: four repeated hourly values cannot represent actual 15-minute tariff dynamics. Delayed measured production can support retrospective explanation, but not live forecasting; even a 48h lag does not overcome a one-month publication delay.

### Insights

The following relationships describe **already fixed day-ahead prices and subsequently measured production**, not real-time price setting. They are candidate signals for a future availability-aware forecasting study, not proof of predictive or causal performance. Price-only figures end on 30.10.2026; joint production/price figures end on 31.08.2026. Plot labels retain the notebook's German terminology; the English captions explain each chart.

#### Daily patterns reflect anticipated demand and production

**Seasonal daily profiles, 2015–2026.** Each line is the hourly **median**, and the shaded area is **P25–P75 (the middle 50%)**, not ±25% of the median or a confidence interval. Morning and evening peaks and a midday dip are consistent with expected demand and solar supply. The midday dip is especially clear on spring/summer weekends. Weekdays include holidays; the multi-year profiles mix price regimes, so not every weekday's minimum is at noon.

![Seasonal hourly median price profiles with interquartile bands, split by weekday and weekend](figures/eda/03_tagesprofile_perzentile.png)

**Sensitivity to 2022–2023.** Removing these high-price years preserves the main shape while reducing the mean winter IQR width from **75.29 to 45.64 EUR/MWh**. Without these years, weekend median prices at 14:00 are **12.93 in spring** and **18.21 in summer**. Weekday minima in this longer historical sample are still overnight, so “midday is always cheapest” would overstate the evidence. This comparison tests robustness; it does not justify discarding crisis years from training automatically.

![The same seasonal median and interquartile profiles excluding 2022 and 2023](figures/eda/03b_tagesprofile_perzentile_ohne_2022_2023.png)

#### Weekly structure adds information beyond the hour of day

**Price by weekday and season, since 2024.** Seasonal median lines and P25–P75 bands show the weekend discount, most pronounced in spring and summer. Across all seasons, the median is **107.99 EUR/MWh on Tuesday**, **94.92 on Saturday** and **87.08 on Sunday**. These are descriptive pooled comparisons, not a controlled estimate of demand effects. Weekday, season and their interaction are therefore candidate features.

![Seasonal weekly price patterns with median lines and 25th–75th percentile bands](figures/eda/04_wochenmuster.png)

#### Intraday spreads create opportunities, but most first-day information is public

**Mean daily maximum-minus-minimum spread by month.** Annual mean daily spreads were **19.81–24.62 EUR/MWh in 2015–2020**, **61.61 in 2024**, **79.38 in 2025** and **110.47 in Jan–Oct 2026**. Negative-price hours increased from **9 in 2015** to **292 in 2024** and **303 in 2025**. This supports studying load shifting, not a guaranteed savings amount: the first delivery day's prices are already known, and battery constraints and retail tariffs reduce the usable margin. A second-day forecast must demonstrate **incremental** value over that known-price baseline.

![Monthly mean daily price spread in EUR per MWh](figures/eda/05_tagesspanne.png)

#### Seasonal generation and trade motivate a regional market view

**Monthly production by source and the Swiss energy balance.** Individual producer lines make the mix readable without stacked areas. In the comparable period, monthly net trade ranges from **−4.04 TWh (export)** to **+1.93 TWh (import)**. Summer export and winter import patterns motivate including neighbouring markets; the balance alone does not establish their price effect.

![Separate monthly production lines and the corresponding load and import/export balance](figures/eda/08_strommix_monat.png)

**Production profiles in winter and summer 2025.** Each producer has its own median line and P25–P75 band. PV has a much stronger daytime peak in summer; controllable hydro and storage have different schedules. “Winter 2025” combines January, February and December of the same calendar year rather than one consecutive winter. These are observed production profiles, not forecasts available before the auction.

![Winter and summer 2025 generation profiles, each producer shown separately with an interquartile band](figures/eda/09_erzeugung_tagesprofil.png)

**PV share of Swiss generation.** The share rose from **7.41% in 2023** to **8.39% in 2024** and **13.43% in 2025**. **18.41% in Jan–Aug 2026** is a partial-year share with different seasonal coverage, not a directly comparable annual growth observation. The expansion motivates examining midday prices but does not by itself prove that future negative prices will become more frequent.

![Annual PV production share, with partial-year 2026 distinguished in a separate color](figures/eda/07_pv_anteil.png)

#### Residual load contains a strong retrospective price signal

**Rank correlations, since 2024.** Residual load (**load − PV − wind**) has **Spearman ρ = 0.548** with the price, compared with **0.262 for load alone**, **−0.292 for PV**, and **0.039 for Swiss wind**. This is the strongest absolute correlation among the Swiss variables examined, not an out-of-sample feature ranking. Pumped-storage consumption is signed negative: its positive ρ means less pumping at higher prices, consistent with dispatch responding to prices.

![Spearman correlations between the fixed day-ahead price and measured Swiss variables](figures/eda/10_korrelationen.png)

**Price against residual load, PV and trade.** The scatter plots use hourly observations; black curves are median prices in 25 equally populated bins. The residual-load gradient is clearer than the trade relationship (**ρ = 0.060**). For readability, scatter price axes show P0.2–P99.8; no corresponding observations are removed from the dataset or correlation calculation. Time of day, season, hydrology and regional market conditions remain confounders.

![Measured residual load, PV and trade versus price with binned median curves](figures/eda/11_scatter_treiber.png)

**Summer versus winter: residual load alongside prices.** On the joint period from 2024 to August 2026, the summer hourly median price falls to **27.83 EUR/MWh at 14:00**, compared with **113.92 at 20:00**; its profile range is **86.10 EUR/MWh**. The winter range is **33.49 EUR/MWh**, with higher breakfast/evening prices and a shallower midday dip. Residual load has a matching summer trough. This is consistent with the auction anticipating sunshine and demand; measured residual load did not change a price already fixed the day before. The separate axes have different units, and IQR bands are not forecast intervals.

![Winter and summer hourly median residual load above the corresponding fixed day-ahead price profiles](figures/eda/14_residuallast_preis_tagesprofil.png)

#### Solar is a stronger Swiss proxy than wind, not yet a validated predictor

**Midday price by measured PV output, since 2024.** The median price generally decreases across 0.5-GW PV bins for 11:00–15:00 local time (at least 30 observations per bin). Restricting the hours reduces, but does not remove, time-of-day confounding. The result is consistent with expected next-day sunshine being priced in; it does not prove a simultaneous causal effect.

![Median midday day-ahead price by Swiss PV power bin](figures/eda/12_solar_mittagspreis.png)

**Direct PV–wind comparison.** On the same joint period, PV's **ρ = −0.292** is substantially stronger in absolute terms than Swiss wind's **ρ = +0.039**. The black lines show binned median prices; the price axes use the same P0.2–P99.8 limits as the driver scatter. This supports investigating solar and residual-load forecasts, but does not show that wind forecasts are unhelpful in Germany or elsewhere. Stronger retrospective correlation alone is not a forecast-accuracy result.

![Side-by-side Swiss solar and wind relationships with price on the same price scale](figures/eda/15_solar_wind.png)

#### Persistence is useful, but accuracy must be tested at the real issuance time

**Hourly price autocorrelation, since 2024.** Correlations are **r = 0.847 at 24h**, **0.744 at 48h**, and **0.813 at 168h**. These daily/weekly dependencies motivate persistence baselines. The notebook computes annual descriptive nMAE for all three lags: in **2025**, **14.16% (24h)**, **19.29% (48h)** and **15.98% (168h)**. These are not issuance-aware D+2 backtest results.

![Hourly Swiss price autocorrelation with daily, two-day and weekly lags highlighted](figures/eda/13_autokorrelation.png)

**Forecast timing matters.** After the auction on day D, prices for D+1 are known. A target hour on D+2 has its 24h-lag on D+1, so that lag can already be available; 48h persistence is not the only permitted benchmark. Check publication times for each target hour, especially for rolling horizons and daylight-saving transitions. Delayed Pronovo data cannot be used contemporaneously or with only a short lag. The existing [analysis pipeline](analysis/README.md) studies a pre-auction next-day task; its reported scores do **not** validate the proposed D+2 extension.

**Is the data suitable?** The complete price series is suitable as a target and supports clear temporal signals. Pronovo supports retrospective explanation after schema harmonization, but a deployable extended profile additionally needs archived, issuance-time-available D+2 forecasts and regional information. The next modeling step is to verify that availability and evaluate the second-day prediction separately, then compare scheduling against an EMS using only published prices. This EDA establishes relevant patterns and limitations, not a proven 48h forecasting product.

---

# 3. Data Preparation
*Data preparation (also known as feature engineering) consists of steps such as cleaning, filtering, joining, [aggregating](https://www.geeksforgeeks.org/python/grouping-and-aggregating-with-pandas/), [normalizing](https://www.geeksforgeeks.org/python/data-normalization-with-pandas/), and transforming the data. The goal is to have one dataset that contains all features and is ready to be used in the follow-up data modeling. All data preparation is contained in one notebook that may use modular code. Data preparation is not a deliverable but goes hand in hand with the follow-up step of data modeling.*

- Notebook to run the data preparation: [02_data_preparation.ipynb](notebooks/02_data_preparation.ipynb)
- Reusable, modular code is outsourced to separate `.py` files: [src/data_preparation.py](src/data_preparation.py)

## Features

*List and describe each feature.*

| Feature | Description |
|---------|-------------|
|         |             |
|         |             |
|         |             |

---

# 4. Data Modeling

> ⚠️ **Deliverable - 03.12.2026:** Summarize the data modeling approach.

## Concept
*Describe the data modeling approach in a few sentences. Which major tasks have to be performed? Which types of models are used (why)? What are the results? The goal of DSPRO1 is not to implement complex ML algorithms but to tell a coherent data story. A dashboard visualisation or a classical statistical analysis cab be sufficient in terms of data modeling.*

## Implementation
*Make a reference to the notebook and scripts used for performing the data modeling. Sketch a diagram that contains all major tasks and notes for the corresponding implementation.*

- Notebook to run the data modeling: [03_modeling.ipynb](notebooks/03_modeling.ipynb)
- Reusable, modular code is outsourced to separate `.py` files: [src/data_modeling.py](src/data_modeling.py)


# 5. Evaluation

> ⚠️ **Deliverable - 10.12.2026:** Discuss your results in the light of the project goals.

- Notebook to run the evaluation: [04_evaluation.ipynb](notebooks/04_evaluation.ipynb)

- All visualizations can be found in `figures/eval`.


## Results
*List major results. Results often come in the form of evaluation metrics.  A short introduction can be found [here](https://www.ibm.com/think/topics/model-evaluation).*

## Interpretation
*Interpret results in the light of the problem statement. Could you solve the problem? An important issue in the interpretation is practical applicability. Is your solution generalizable?*

## Limitations
*Describe weaknesses of your approach or the underlying data.*

---

# 6. Sustainable Development Goals

> ⚠️ **Deliverable - 17.12.2026:** Describe your results in the light of sustainability.

*The United Nations distinguishes 17 Sustainable Development Goals ([link](https://sdgs.un.org/goals)). Put the results of your project in relation to at least one of these goals. A four-sentence description is sufficient.*
