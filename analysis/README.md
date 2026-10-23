# Analyse: Preistreiber & 24h-Prognose des Schweizer Day-Ahead-Preises

Ziel 1 des Projekts: die europäischen Treiber des CH-Day-Ahead-Preises erklären und ihn 24 h
im Voraus mit einem **nMAE unter 10 %** prognostizieren.
Der Rechner und das Dashboard sind nicht Teil dieses Ordners.

## Ablauf

| Schritt | Skript | Ergebnis |
|---|---|---|
| 1 Datenpipeline | `01_fetch_data.py` | `data/processed/hourly.csv` (Preise, Erzeugung, Prognosen, Austausch, Wetter; stündlich, UTC) |
| 2 EDA / Preistreiber | `02_eda.py` | `output/eda/` (Grafiken, Korrelationen, `summary.md`) |
| 3 Features & Baselines | `03_baseline.py` | `output/baseline/` (Persistenz, Profil, Ridge) |
| 4 LightGBM, Tuning, SHAP | `04_model.py` | `output/model/` (Metriken, Grafiken, SHAP, gespeichertes Modell) |
| 5 Rollende 24h-Prognose | `05_forecast.py` | `web/public/forecast/forecast.json` → Dashboard unter `/analyse` |

Quellen für den Bericht: [`QUELLEN.txt`](QUELLEN.txt).

```bash
cd analysis
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# macOS: LightGBM braucht OpenMP →  brew install libomp
.venv/bin/python 01_fetch_data.py   # dauert wegen Rate-Limit von energy-charts (HTTP 429) eine Weile; Cache in data/raw/
.venv/bin/python 02_eda.py
.venv/bin/python 03_baseline.py
.venv/bin/python 04_model.py        # --trials N für die Grösse der Hyperparameter-Suche
.venv/bin/python 05_forecast.py     # nach 01: Prognose ab jetzt für 24 h (--now "YYYY-MM-DD HH:MM" für einen anderen Zeitpunkt)
```

Das Dashboard (`web/src/app/pages/analysis/`) liest nur `web/public/forecast/forecast.json`. Diese Datei wird mit
der Website ausgeliefert, also für eine neue Prognose 01 + 05 ausführen und die Datei neu deployen. Sie deckt
jetzt + 30 h ab: Das Dashboard rückt die 24-h-Simulation mit der Uhr nach und bleibt so bis 6 h nach dem Lauf
aktuell. Der CH-Day-Ahead-Preis ist ein Stundenpreis; in der 15-min-Ansicht hat jede Stunde vier gleiche Werte.

## Methode

**Prognosesituation.** Am Vortag D-1 vor dem Gebotsschluss werden die 24 Stundenpreise von Tag D
vorhergesagt. Damit kein Leakage entsteht, gilt Folgendes:
- Preise (CH, DE, FR, IT-Nord, AT) gehen nur als Lags ≥ 24 h ein (24/48/72/168 h, Vortagesmittel, 7-Tage-Mittel).
- Ist-Erzeugung und Austausch gehen nur mit Lag ≥ 48 h ein.
- Für Stunde t dürfen direkt die Day-Ahead-Prognosen der Netzbetreiber (Solar, Wind, Last, daraus die
  prognostizierte Residuallast) und die Wetterprognosen des Vortageslaufs (Open-Meteo `previous_day1`) verwendet werden.
- Dazu kommen Kalender-Features: Stunde, Wochentag, Wochenende, Feiertag sowie Monat und Tag im Jahr (Sinus/Kosinus).

**Metrik.** `nMAE = MAE / mittlerer Absolutpreis`. Einen MAPE kann man bei Preisen um 0 bzw. bei
negativen Preisen nicht sinnvoll berechnen.

**Validierung.**
- Tuning: Zufallssuche mit `TimeSeriesSplit` (5 Folds à 60 Tage, 24 h Lücke) auf den Daten vor dem 1.10.2025.
- Holdout ab 1.10.2025 als Walk-Forward: Für jeden Monat wird mit allen Daten davor neu trainiert. Es wird nie zufällig gemischt.

**Erklärung.** SHAP-Werte (TreeExplainer) je Stundenprognose, zusätzlich zu Treibergruppen
zusammengefasst (Preisniveau, Nachbarpreise, Solar, Wind, Last/Residuallast, Wasserkraft/Austausch, Kalender).

**Zielgrösse des Modells.** LightGBM sagt die Änderung gegenüber dem Vortagespreis zur selben Stunde voraus
(`y − lag_ch_24`). Der Grund: Das Preisniveau stieg von Ø 76 EUR/MWh (2024) auf Ø 128 EUR/MWh (2026), und
Baum-Modelle können nicht extrapolieren. Die wichtigsten Features sind deshalb ebenfalls Änderungen gegenüber
dem Vortag (`*_d24`), z. B. der Unterschied in der prognostizierten Residuallast. Diese Wahl wurde per
Cross-Validation vor dem Holdout getroffen (CV-MAE 11.7 → 9.9 EUR/MWh).

## Ergebnisse (Stand 22.10.2026)

Holdout 01.10.2025 – 23.10.2026 (9'312 Stunden, Ø Absolutpreis 126 EUR/MWh):

| Modell | MAE [EUR/MWh] | nMAE |
|---|---:|---:|
| **LightGBM** | **10.99** | **8.7 %** |
| LightGBM ohne Netzbetreiber-Prognosen (nur Wetter) | 13.06 | 10.3 % |
| Ridge-Regression (gleiche Features) | 12.82 | 10.2 % |
| Profil 14 Tage (heutiger Fallback im Rechner) | 18.10 | 14.3 % |
| Persistenz 24 h (Baseline) | 18.15 | 14.4 % |

- **Ziel nMAE < 10 % erreicht.** Der Fehler liegt 39 % unter der Persistenz-Baseline.
- Winter: 4–6 %. Frühling/Sommer: 9–18 %, der schwierigste Monat ist April 2026. Fehler entstehen vor allem durch
  Mittags-Preiseinbrüche mit stark negativen Preisen bei viel Solar über Feiertage/Wochenenden. Das Modell
  trifft die Richtung, aber nicht die volle Tiefe (siehe `output/model/02_beispielwochen.png`).
- Die wichtigsten Treiber laut SHAP:
  1. die prognostizierte Residuallast (DE+CH+FR+IT+AT) und ihre Änderung gegenüber dem Vortag,
  2. das Vortagesniveau (mit Rückkehr zum Mittel),
  3. die Nachbarpreise,
  4. Wasserkraft/Austausch, Wind und Solar.

**Einschränkungen**
- Die Day-Ahead-Prognosen der Netzbetreiber werden laut ENTSO-E-Regeln teils erst bis 18:00 am Vortag publiziert.
  Wird vor dem CH-Gebotsschluss (11:00) prognostiziert, ist nicht jede Reihe garantiert schon da. Die Variante
  „ohne Netzbetreiber-Prognosen“ (10.3 %) ist deshalb die konservative Untergrenze.
- Der CH-Day-Ahead-Preis wird auf energy-charts auch nach dem 1.10.2025 stündlich ausgewiesen. Für einen
  15-min-Tarif (EKZ) gilt die Prognose deshalb je Stunde.
- Gaspreise (TTF) und CO₂-Preise sind nicht frei per API verfügbar. Sie stecken nur indirekt im Preisniveau der Vortage.

Details: `output/eda/summary.md`, `output/baseline/summary.md`, `output/model/summary.md`.
Das finale Modell (auf allen Daten trainiert) liegt in `output/model/lgbm_ch_dayahead.txt`
(Basis und Feature-Liste in `feature_columns.json`).
