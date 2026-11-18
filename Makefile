# AI-EMS – common commands
.PHONY: data dev api api-setup api-test build docker-up docker-down cluster-up cluster-down manifests

data:          ## Fetch data snapshot into web/public/data (~5 min, API throttling)
	node fetcher/fetch-data.mjs

dev:           ## Angular dev server on http://localhost:4200 (/api → localhost:8000)
	cd web && npx ng serve

api:           ## Python backend on http://localhost:8000 (once: make api-setup)
	cd api && .venv/bin/uvicorn app.main:app --reload --port 8000

api-setup:
	cd api && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest

api-test:      ## MPC tests
	cd api && .venv/bin/python -m pytest -q tests

build:         ## Production build
	cd web && npx ng build

docker-up:     ## Docker environment (web + fetcher) on http://localhost:$${WEB_PORT:-8080}
	docker compose up --build -d

docker-down:
	docker compose down

cluster-up:    ## kind + ingress-nginx + Argo CD + Application (GITHUB_TOKEN for private repo)
	./scripts/cluster-up.sh

cluster-down:
	./scripts/cluster-down.sh

manifests:     ## Render production manifests
	kubectl kustomize k8s/overlays/prod
