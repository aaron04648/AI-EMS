#!/usr/bin/env bash
# Deletes the local production cluster again.
set -euo pipefail
kind delete cluster --name ai-ems
