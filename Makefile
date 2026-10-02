# AI-EMS – häufige Befehle
.PHONY: data dev api api-setup api-test build docker-up docker-down cluster-up cluster-down manifests

data:          ## Daten-Snapshot nach web/public/data holen (~5 min, API-Drosselung)
	node fetcher/fetch-data.mjs

dev:           ## Angular-Dev-Server auf http://localhost:4200 (/api → localhost:8000)
	cd web && npx ng serve

api:           ## Python-Backend auf http://localhost:8000 (einmalig: make api-setup)
	cd api && .venv/bin/uvicorn app.main:app --reload --port 8000

api-setup:
	cd api && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest

api-test:      ## Tests der MPC
	cd api && .venv/bin/python -m pytest -q tests

build:         ## Produktions-Build
	cd web && npx ng build

docker-up:     ## Docker-Umgebung (Web + Fetcher) auf http://localhost:$${WEB_PORT:-8080}
	docker compose up --build -d

docker-down:
	docker compose down

cluster-up:    ## kind + ingress-nginx + Argo CD + Application (GITHUB_TOKEN für privates Repo)
	./scripts/cluster-up.sh

cluster-down:
	./scripts/cluster-down.sh

manifests:     ## Produktiv-Manifeste rendern
	kubectl kustomize k8s/overlays/prod
