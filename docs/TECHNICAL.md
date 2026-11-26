# AI-EMS – Technical documentation

> The project report (CRISP-DM) is in the [README](../README.md).

Website about **dynamic electricity tariffs**: it analyses the electricity prices from
[energy-charts.info](https://energy-charts.info), first for Switzerland and then in a European comparison,
and explains why prices rise and fall. A standalone calculator uses **model predictive
control (MPC)** to plan when a home battery charges, discharges, buys or sells electricity.

| Page | Content |
|---|---|
| `/` Overview | Landing page with the current EKZ price, daily profile, price drivers and a Europe teaser |
| `/analysis` | Single-screen dashboard: key figures for the next 24 h, actual price up to now and LightGBM forecast of the next 96 quarter hours (views 24 h, 7 days, reasons), quarter-hour list on the right. Data from `analysis/05_forecast.py` |
| `/calculator` | Separate app in a new tab. On the left a sidebar with the inputs (location, PV, battery, household, tariff, grid), on the right three views: **Meteo** (ICON-CH2 weather map with time slider: clouds, sun, precipitation), **Exchange** (96 quarter-hour tariffs per day with day slider and MPC actions), **Statistics** (chart sheet: charging plan, backtest, PV model). Explanations are only available via **?** buttons. |

### Calculator: PV forecast + MPC

Method following section 3.5 in Ruddick et al. (2024),
[Real-world validation of safe reinforcement learning, model predictive control and decision tree-based home energy management systems](https://www.sciencedirect.com/science/article/pii/S2666546824001149)
([arXiv](https://arxiv.org/abs/2408.07435)):

1. **PV model:** [PVGIS](https://re.jrc.ec.europa.eu/pvg_tools/) provides hourly series 2020–2023 at the location for the
   chosen orientation. A gradient boosting regressor (like the LightGBM in the paper) learns from it: global irradiance,
   sun position and temperature → system power. Test on 2023: R² ≈ 0.999.
2. **Weather from MeteoSwiss:** The ICON-CH1/CH2 forecasts (MeteoSwiss Open Data, via Open-Meteo) go through the
   PV model. In the backtest, billing uses measurements from the nearest **SwissMetNet station** (`data.geo.admin.ch`).
3. **Statistical forecasts:** Load from standard load profile H0 with a seasonal factor. Prices: published EKZ tariffs
   (`integrated_400D`, by 18:00 for the following day), afterwards the mean of the same quarter hour over the last 14 days,
   separately for weekday and weekend.
4. **MPC as MILP** (paper eq. 10–12, solved with HiGHS): Every 15 min the system is optimised over 24 h, only the
   first step is applied. Constraints: storage model with efficiency, power and grid limits, binary variable against
   simultaneous charging and discharging, target state of charge at the end of the horizon.
5. **Backtest as in the paper:** No battery, the self-consumption rule (RBC), the MPC and the optimum
   with perfect foresight are compared, all under the same conditions.

Deviations from the paper:
- The PV forecast uses weather data (the paper deliberately does without it).
- There is no EV charging point.
- Optionally, wear costs per kWh throughput are taken into account.
- A small tie-breaker prefers early storage at equal cost. This makes the MPC robust against
  PV forecast errors.

## Technology

- **Frontend:** Angular 20 (standalone, zoneless, signals), SCSS. The design system (Apple style) comes from the
  projects *company-homepage*, *atelier-ritzo-homepage* and *munizipal*. The charts are custom SVG components without a chart library.
- **Backend:** Python 3.12, FastAPI, NumPy/pandas, scikit-learn (PV model), SciPy/HiGHS (MILP).
  Endpoints: `POST /api/simulate`, `GET /api/weather-map` (0.15° grid over Switzerland, cached for 30 min),
  `GET /api/geocode?q=` (swisstopo), `GET /api/health`.
- **Light/dark:** All colours are CSS variables (`styles.scss`, `:root` and `[data-theme='dark']`), the charts
  use separate shades of the dataviz palette per mode. The toggle remembers the choice, otherwise the
  system setting applies.
- **Map:** Leaflet with the grey national map from swisstopo (WMTS). The weather overlay is interpolated in the browser as a canvas
  from the grid.
- **Data:** `fetcher/fetch-data.mjs` (Node, no dependencies) fetches the data and stores it as JSON snapshots in `data/`:
  - `api.energy-charts.info`: day-ahead prices (`/price`) and generation (`/public_power`), CC BY 4.0
  - `api.tariffs.ekz.ch`: `integrated_400D` (dynamic), `integrated_400ST` (standard), `electricity_dynamic`

  Why snapshots instead of live queries in the browser? energy-charts.info does not allow browser requests from other domains (CORS)
  and throttles many requests (HTTP 429). That is why only the fetcher makes requests, throttled and with retry.

```
AI-EMS/
├── web/                 Angular app (+ Dockerfile, nginx configuration in web/deploy/)
├── api/                 Python backend: PV model, MPC, backtest (+ Dockerfile, tests)
├── fetcher/             data fetcher (+ Dockerfile)
├── docker-compose.yml   local Docker environment
├── k8s/base             web deployment (nginx + fetcher sidecar), API deployment, services, ingress
├── k8s/overlays/prod    production overlay – image tags are set by CI
├── deploy/argocd        Argo CD application (auto-sync on main)
├── deploy/kind          local Kubernetes cluster
├── scripts/             cluster-up.sh / cluster-down.sh
└── .github/workflows    deploy.yml (main → GHCR → GitOps → Pages), ci.yml (PRs)
```

## Infrastructure

### 1. Development

```bash
make data       # fetch data into web/public/data (~5 min due to API throttling)
make api-setup  # once: Python venv for the backend
make api        # backend on http://localhost:8000
make dev        # http://localhost:4200 (forwards /api to the backend)
make api-test   # MPC tests
```

### 2. Docker environment

```bash
docker compose up --build -d                  # http://localhost:8080
WEB_PORT=8088 docker compose up --build -d    # if 8080 is in use
```

`web` (nginx, unprivileged) and `fetcher` (updates every 3 h) share the volume `data`. nginx forwards `/api/`
to the container `api` (FastAPI). The API reads the day-ahead price from the snapshot under `/data` and thus spares
the throttled energy-charts API. While the first
run is in progress, nginx serves the snapshot baked in at build time (`web/public/data`, if present).

### 3. Production: Kubernetes + Argo CD (GitOps)

```
git push main ──► GitHub Actions (tests) ──► ghcr.io/aaron04648/ai-ems-{web,fetcher,api}:<sha>
                        │
                        └─► commit: k8s/overlays/prod/kustomization.yaml (newTag: <sha>)
                                          │
                     Argo CD (auto-sync, prune, selfHeal) ◄┘ ──► deployment ai-ems rolls out
```

Set up the cluster (locally with kind; any other cluster works the same way):

```bash
brew install kind                                  # once
GITHUB_USER=aaron04648 GITHUB_TOKEN=<PAT> make cluster-up
# → http://ai-ems.localhost
```

The PAT needs `repo` and `read:packages` as long as the repository and images are private. The script uses it to store
the Argo CD repo credentials and the pull secret `ghcr-pull`. Without a token it only works with a public repo
and public images.

### 4. GitHub Pages: the public domain

On every push to `main` and additionally every 6 h (fresh data), `deploy.yml` deploys the site with baked-in
data to **https://aaron04648.github.io/AI-EMS/**. GitHub Pages only hosts static files. The calculator needs
the Python backend and therefore only works in the Docker and Kubernetes environment. On Pages it shows a
corresponding notice.

One-time setup: *Settings → Pages → Source: GitHub Actions*. For private repositories GitHub Pages
requires a Pro/Team plan. Alternatively, make the repository public.

### Contributors

The contributors are shown in the footer and maintained in `web/src/app/core/site.config.ts`.
