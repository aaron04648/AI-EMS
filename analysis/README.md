# Analysis: price drivers & 24h forecast of the Swiss day-ahead price

Goal 1 of the project: explain the European drivers of the CH day-ahead price and forecast it 24 h
ahead with an **nMAE below 10 %**.
The calculator and the dashboard are not part of this folder.

## Workflow

| Step | Script | Result |
|---|---|---|
| 1 Data pipeline | `01_fetch_data.py` | `data/processed/hourly.csv` (prices, generation, forecasts, exchange, weather; hourly, UTC) |
| 2 EDA / price drivers | `02_eda.py` | `output/eda/` (charts, correlations, `summary.md`) |
| 3 Features & baselines | `03_baseline.py` | `output/baseline/` (persistence, profile, ridge) |
| 4 LightGBM, tuning, SHAP | `04_model.py` | `output/model/` (metrics, charts, SHAP, saved model) |
| 5 Rolling 24h forecast | `05_forecast.py` | `web/public/forecast/forecast.json` → dashboard at `/analysis` |
| 6 Target value analysis nMAE | `06_target_value.py` | `output/target_value/` (volatility 2015–2026, naive nMAE by year/month, rMAE vs. literature) |

Sources for the report: [`SOURCES.txt`](SOURCES.txt).

```bash
cd analysis
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# macOS: LightGBM needs OpenMP →  brew install libomp
.venv/bin/python 01_fetch_data.py   # takes a while due to the energy-charts rate limit (HTTP 429); cache in data/raw/
.venv/bin/python 02_eda.py
.venv/bin/python 03_baseline.py
.venv/bin/python 04_model.py        # --trials N for the size of the hyperparameter search
.venv/bin/python 05_forecast.py     # after 01: forecast from now for 24 h (--now "YYYY-MM-DD HH:MM" for a different point in time)
.venv/bin/python 06_target_value.py # after 04: loads CH prices from 2015 (own cache) and justifies the target value
```

The dashboard (`web/src/app/pages/analysis/`) only reads `web/public/forecast/forecast.json`. This file is shipped
with the website, so for a new forecast run 01 + 05 and redeploy the file. It covers
now + 30 h: the dashboard advances the 24 h simulation with the clock and thus stays current until 6 h after the run.
The CH day-ahead price is an hourly price; in the 15 min view every hour has four identical values.

## Method

**Forecast situation.** On the previous day D-1, before gate closure, the 24 hourly prices of day D
are predicted. To avoid leakage, the following applies:
- Prices (CH, DE, FR, IT-North, AT) only enter as lags ≥ 24 h (24/48/72/168 h, previous-day mean, 7-day mean).
- Actual generation and exchange only enter with a lag ≥ 48 h.
- For hour t, the day-ahead forecasts of the grid operators (solar, wind, load, and the forecast residual
  load derived from them) and the weather forecasts of the previous-day run (Open-Meteo `previous_day1`) may be used directly.
- In addition there are calendar features: hour, weekday, weekend, public holiday, as well as month and day of year (sine/cosine).

**Metric.** `nMAE = MAE / mean absolute price`. A MAPE cannot be computed meaningfully for prices around 0 or
negative prices. Additionally `rMAE = MAE_model / MAE_persistence-168-h`
(Lago et al. 2021): the rMAE is comparable between periods, the nMAE is not (see "Target value").

**Target value (`06_target_value.py`).** The nMAE depends strongly on how much prices fluctuate in the
evaluation period. Naive weekly persistence reaches 14–25 % per year in 2015–2026
(avg. 17.6 %), and 7–46 % per month. In the holdout it is 17.7 %, i.e. exactly at the long-term mean.
According to Lago et al. (2021), state-of-the-art models achieve an rMAE of 0.38–0.68. With
average volatility this corresponds to an nMAE of 6.7–12.0 %. **nMAE < 10 %** corresponds to
an rMAE of ≈ 0.57. The goal is therefore demanding but realistic. It only holds over
≥ 12 months (full seasonal cycle) and only with the rMAE as a check.

**Validation.**
- Tuning: random search with `TimeSeriesSplit` (5 folds of 60 days, 24 h gap) on the data before 1 Oct 2025.
- Holdout from 1 Oct 2025 as walk-forward: for each month the model is retrained on all data before it. Data is never shuffled randomly.

**Explanation.** SHAP values (TreeExplainer) per hourly forecast, additionally aggregated into driver groups
(price level, neighbouring prices, solar, wind, load/residual load, hydro/exchange, calendar).

**Model target.** LightGBM predicts the change relative to the previous day's price at the same hour
(`y − lag_ch_24`). The reason: the price level rose from avg. 76 EUR/MWh (2024) to avg. 128 EUR/MWh (2026), and
tree models cannot extrapolate. The most important features are therefore also changes relative to
the previous day (`*_d24`), e.g. the difference in forecast residual load. This choice was made via
cross-validation before the holdout (CV MAE 11.7 → 9.9 EUR/MWh).

## Results (as of 22 Oct 2026)

Holdout 01 Oct 2025 – 23 Oct 2026 (9,312 hours, avg. absolute price 126 EUR/MWh):

| Model | MAE [EUR/MWh] | nMAE |
|---|---:|---:|
| **LightGBM** | **10.99** | **8.7 %** |
| LightGBM without grid operator forecasts (weather only) | 13.06 | 10.3 % |
| Ridge regression (same features) | 12.82 | 10.2 % |
| Profile 14 days (current fallback in the calculator) | 18.10 | 14.3 % |
| Persistence 24 h (baseline) | 18.15 | 14.4 % |

- **Goal nMAE < 10 % achieved.** The error is 39 % below the persistence baseline. The rMAE
  relative to weekly persistence is 0.49 (without grid operator forecasts 0.58). This is within the
  state-of-the-art range of Lago et al. (2021): 0.38–0.68.
- Winter: 4–6 %. Spring/summer: 9–18 %, the hardest month is April 2026. Errors arise mainly from
  midday price drops with strongly negative prices during high solar over holidays/weekends. The model
  gets the direction right, but not the full depth (see `output/model/02_example_weeks.png`).
- The most important drivers according to SHAP:
  1. the forecast residual load (DE+CH+FR+IT+AT) and its change relative to the previous day,
  2. the previous-day level (with mean reversion),
  3. the neighbouring prices,
  4. hydro/exchange, wind and solar.

**Limitations**
- According to ENTSO-E rules, the grid operators' day-ahead forecasts are partly only published by 18:00 on the previous day.
  If the forecast is made before CH gate closure (11:00), not every series is guaranteed to be available yet. The variant
  "without grid operator forecasts" (10.3 %) is therefore the conservative lower bound.
- On energy-charts the CH day-ahead price is still reported hourly after 1 Oct 2025. For a
  15 min tariff (EKZ) the forecast therefore applies per hour.
- Gas prices (TTF) and CO₂ prices are not freely available via API. They are only indirectly reflected in the price level of previous days.

Details: `output/eda/summary.md`, `output/baseline/summary.md`, `output/model/summary.md`.
The final model (trained on all data) is in `output/model/lgbm_ch_dayahead.txt`
(base and feature list in `feature_columns.json`).
