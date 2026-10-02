#!/usr/bin/env bash
# Löscht den lokalen Produktiv-Cluster wieder.
set -euo pipefail
kind delete cluster --name ai-ems
