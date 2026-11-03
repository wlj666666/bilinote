#!/usr/bin/env bash
# Build and prepare first; only replace the running services after both succeed.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
case "${1:-gpu}" in
  gpu) compose_file=docker-compose.gpu.yml ;;
  cpu) compose_file=docker-compose.yml ;;
  *) echo '用法: bash deploy.sh [gpu|cpu]' >&2; exit 2 ;;
esac
docker compose -f "$compose_file" build
docker compose -f "$compose_file" run --rm --no-deps backend python3 scripts/prepare_embeddings.py
# Recreate nginx too: it must resolve the replacement backend/frontend container IPs.
docker compose -f "$compose_file" up -d --force-recreate --wait --wait-timeout 120
docker compose -f "$compose_file" ps
