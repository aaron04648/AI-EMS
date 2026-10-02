# AI-EMS – Technische Dokumentation

> Der Projektbericht (CRISP-DM) steht in der [README](../README.md).

Webseite zu **dynamischen Stromtarifen**: Sie analysiert die Strompreise von
[energy-charts.info](https://energy-charts.info), zuerst für die Schweiz und dann im europäischen Vergleich,
und erklärt, warum Preise steigen und sinken. Ein eigenständiger Rechner plant mit einer **modellprädiktiven
Regelung (MPC)**, wann eine Heimbatterie lädt, entlädt, Strom kauft oder verkauft.

| Seite | Inhalt |
|---|---|
| `/` Übersicht | Landing Page mit aktuellem EKZ-Preis, Tagesverlauf, Preistreibern und Europa-Teaser |
| `/analyse` | Schweiz: Monatsmittel, Tagesprofil, Erzeugungsmix, Korrelationen · Europa: 14 Gebotszonen · EKZ vs. Börse · Fazit |
| `/rechner` | Eigene App im neuen Tab: Sidebar mit Inputs (Standort, PV, Batterie, Haushalt, Tarif, Netz) → Fahrplan ab jetzt + Backtest der letzten Tage |

### Rechner: PV-Prognose + MPC

Methode nach Abschnitt 3.5 in Ruddick et al. (2024),
[Real-world validation of safe reinforcement learning, model predictive control and decision tree-based home energy management systems](https://www.sciencedirect.com/science/article/pii/S2666546824001149)
([arXiv](https://arxiv.org/abs/2408.07435)):

1. **PV-Modell:** [PVGIS](https://re.jrc.ec.europa.eu/pvg_tools/) liefert stündliche Reihen 2020–2023 am Standort für die
   gewählte Ausrichtung. Ein Gradient-Boosting-Regressor (wie das LightGBM im Paper) lernt daraus: Globalstrahlung,
   Sonnenstand und Temperatur → Leistung der Anlage. Test auf 2023: R² ≈ 0.999.
2. **Wetter von MeteoSwiss:** Die Prognosen ICON-CH1/CH2 (MeteoSwiss Open Data, via Open-Meteo) gehen durch das
   PV-Modell. Im Backtest wird mit Messungen der nächsten **SwissMetNet-Station** abgerechnet (`data.geo.admin.ch`).
3. **Statistische Prognosen:** Last nach Standardlastprofil H0 mit Saisonfaktor. Preise: publizierte EKZ-Tarife
   (`integrated_400D`, bis 18 Uhr für den Folgetag), danach das Mittel derselben Viertelstunde der letzten 14 Tage,
   getrennt nach Werktag und Wochenende.
4. **MPC als MILP** (Paper-Gl. 10–12, gelöst mit HiGHS): Alle 15 min wird über 24 h optimiert, umgesetzt wird nur der
   erste Schritt. Nebenbedingungen: Speichermodell mit Wirkungsgrad, Leistungs- und Netzgrenzen, Binärvariable gegen
   gleichzeitiges Laden und Entladen, Ziel-Speicherstand am Horizontende.
5. **Backtest wie im Paper:** Verglichen werden ohne Batterie, die Eigenverbrauchsregel (RBC), die MPC und das Optimum
   bei perfekter Voraussicht, alle unter denselben Bedingungen.

Abweichungen vom Paper:
- Die PV-Prognose nutzt Wetterdaten (das Paper verzichtet bewusst darauf).
- Es gibt keinen EV-Ladepunkt.
- Optional werden Verschleisskosten pro kWh Durchsatz berücksichtigt.
- Ein kleiner Tie-Breaker bevorzugt bei gleichen Kosten frühes Speichern. Das macht die MPC robust gegen
  PV-Prognosefehler.

## Technology

- **Frontend:** Angular 20 (Standalone, zoneless, Signals), SCSS. Das Design-System (Apple-Stil) stammt aus den
  Projekten *company-homepage*, *atelier-ritzo-homepage* und *munizipal*. Die Charts sind eigene SVG-Komponenten ohne Chart-Bibliothek.
- **Backend:** Python 3.12, FastAPI, NumPy/pandas, scikit-learn (PV-Modell), SciPy/HiGHS (MILP).
  Endpunkte: `POST /api/simulate`, `GET /api/geocode?q=` (swisstopo), `GET /api/health`.
- **Daten:** `fetcher/fetch-data.mjs` (Node, ohne Abhängigkeiten) holt die Daten und legt sie als JSON-Snapshots in `data/` ab:
  - `api.energy-charts.info`: Day-Ahead-Preise (`/price`) und Erzeugung (`/public_power`), CC BY 4.0
  - `api.tariffs.ekz.ch`: `integrated_400D` (Dynamisch), `integrated_400ST` (Standard), `electricity_dynamic`

  Warum Snapshots statt Live-Abfragen im Browser? energy-charts.info erlaubt keine Browser-Anfragen von fremden Domains (CORS)
  und drosselt bei vielen Anfragen (HTTP 429). Deshalb fragt nur der Fetcher an, gedrosselt und mit Retry.

```
AI-EMS/
├── web/                 Angular-App (+ Dockerfile, nginx-Konfiguration in web/deploy/)
├── api/                 Python-Backend: PV-Modell, MPC, Backtest (+ Dockerfile, Tests)
├── fetcher/             Daten-Fetcher (+ Dockerfile)
├── docker-compose.yml   lokale Docker-Umgebung
├── k8s/base             Web-Deployment (nginx + Fetcher-Sidecar), API-Deployment, Services, Ingress
├── k8s/overlays/prod    Produktiv-Overlay – Image-Tags setzt die CI
├── deploy/argocd        Argo-CD-Application (Auto-Sync auf main)
├── deploy/kind          lokaler Kubernetes-Cluster
├── scripts/             cluster-up.sh / cluster-down.sh
└── .github/workflows    deploy.yml (main → GHCR → GitOps → Pages), ci.yml (PRs)
```

## Infrastructur

### 1. Entwicklung

```bash
make data       # Daten nach web/public/data holen (~5 min wegen API-Drosselung)
make api-setup  # einmalig: Python-venv für das Backend
make api        # Backend auf http://localhost:8000
make dev        # http://localhost:4200 (leitet /api an das Backend weiter)
make api-test   # Tests der MPC
```

### 2. Docker-Umgebung

```bash
docker compose up --build -d                  # http://localhost:8080
WEB_PORT=8088 docker compose up --build -d    # falls 8080 belegt ist
```

`web` (nginx, unprivilegiert) und `fetcher` (aktualisiert alle 3 h) teilen sich das Volume `data`. nginx leitet `/api/`
an den Container `api` (FastAPI) weiter. Die API liest den Day-Ahead-Preis aus dem Snapshot unter `/data` und schont so
die gedrosselte energy-charts-API. Solange der erste
Durchlauf läuft, liefert nginx den beim Build eingebackenen Snapshot aus (`web/public/data`, falls vorhanden).

### 3. Produktion: Kubernetes + Argo CD (GitOps)

```
git push main ──► GitHub Actions (Tests) ──► ghcr.io/aaron04648/ai-ems-{web,fetcher,api}:<sha>
                        │
                        └─► commit: k8s/overlays/prod/kustomization.yaml (newTag: <sha>)
                                          │
                     Argo CD (auto-sync, prune, selfHeal) ◄┘ ──► Deployment ai-ems rollt aus
```

Cluster einrichten (lokal mit kind; jeder andere Cluster funktioniert genauso):

```bash
brew install kind                                  # einmalig
GITHUB_USER=aaron04648 GITHUB_TOKEN=<PAT> make cluster-up
# → http://ai-ems.localhost
```

Das PAT braucht `repo` und `read:packages`, solange Repository und Images privat sind. Das Skript hinterlegt damit
die Argo-CD-Repo-Credentials und das Pull-Secret `ghcr-pull`. Ohne Token funktioniert es nur mit öffentlichem Repo
und öffentlichen Images.

### 4. GitHub Pages: die öffentliche Domain

Bei jedem Push auf `main` und zusätzlich alle 6 h (frische Daten) deployt `deploy.yml` die Seite mit eingebackenen
Daten nach **https://aaron04648.github.io/AI-EMS/**. GitHub Pages hostet nur statische Dateien. Der Rechner braucht
das Python-Backend und funktioniert deshalb nur in der Docker- und Kubernetes-Umgebung. Auf Pages zeigt er einen
entsprechenden Hinweis.

Einmalig einrichten: *Settings → Pages → Source: GitHub Actions*. Für private Repositories setzt GitHub Pages
einen Pro/Team-Plan voraus. Alternativ das Repository öffentlich machen.

### Mitwirkende

Die Mitwirkenden werden im Footer angezeigt und in `web/src/app/core/site.config.ts` gepflegt.
