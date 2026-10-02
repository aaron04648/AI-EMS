#!/usr/bin/env bash
# =============================================================
# Richtet den Produktiv-Cluster ein: kind + ingress-nginx + Argo CD
# und registriert die AI-EMS-Application (GitOps auf main).
#
#   GITHUB_USER=aaron04648 GITHUB_TOKEN=<PAT> ./scripts/cluster-up.sh
#
# Das Token (classic PAT mit „repo“ + „read:packages“ oder fine-grained mit
# Contents: read + Packages: read) wird nur benötigt, solange Repository
# und Container-Images privat sind.
# =============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER=ai-ems
ARGOCD_VERSION=${ARGOCD_VERSION:-stable}

need() { command -v "$1" >/dev/null || { echo "✖ $1 fehlt – installieren mit: brew install $2"; exit 1; }; }
need docker docker
need kind kind
need kubectl kubectl

if ! kind get clusters | grep -qx "$CLUSTER"; then
  echo "▶ kind-Cluster $CLUSTER erstellen"
  kind create cluster --config deploy/kind/cluster.yaml
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

echo "▶ ingress-nginx installieren"
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=180s

echo "▶ Argo CD installieren ($ARGOCD_VERSION)"
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/$ARGOCD_VERSION/manifests/install.yaml"
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s

if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  GITHUB_USER=${GITHUB_USER:-aaron04648}
  echo "▶ Zugangsdaten für privates Repo und GHCR hinterlegen"
  kubectl -n argocd apply -f - <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: repo-ai-ems
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  type: git
  url: https://github.com/aaron04648/AI-EMS.git
  username: $GITHUB_USER
  password: $GITHUB_TOKEN
YAML
  kubectl create namespace ai-ems --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n ai-ems create secret docker-registry ghcr-pull \
    --docker-server=ghcr.io --docker-username="$GITHUB_USER" --docker-password="$GITHUB_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -
else
  echo "ℹ Kein GITHUB_TOKEN gesetzt – funktioniert nur mit öffentlichem Repo und öffentlichen Images."
fi

echo "▶ AI-EMS-Application registrieren"
kubectl apply -f deploy/argocd/application.yaml

PASS=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)
cat <<INFO

✔ Fertig.
  Webseite:  http://ai-ems.localhost   (nach dem ersten Sync, ~1–2 min)
  Argo CD:   kubectl -n argocd port-forward svc/argocd-server 8443:443
             → https://localhost:8443  (admin / $PASS)
INFO
