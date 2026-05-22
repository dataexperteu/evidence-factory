#!/usr/bin/env bash
# Start a production Evidence Factory container, auto-selecting a free port.
# Usage: ./scripts/run.sh [image-tag]
#
# Environment overrides:
#   EVIDENCE_FACTORY_IMAGE  Docker image tag  (default: evidence-factory)
#   HOST_IP                 IP shown in the access URL (default: 172.16.85.95)
set -euo pipefail

IMAGE="${EVIDENCE_FACTORY_IMAGE:-evidence-factory}"
HOST_IP="${HOST_IP:-172.16.85.95}"
START_PORT=8080
MAX_PORT=8100

find_free_port() {
    local port=$START_PORT
    while [ "$port" -le "$MAX_PORT" ]; do
        if ! nc -z localhost "$port" 2>/dev/null; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
    done
    echo "ERROR: no free port found in range $START_PORT–$MAX_PORT" >&2
    exit 1
}

PORT=$(find_free_port)

echo "Starting Evidence Factory on port $PORT ..."
docker run -d --rm \
    -p "${PORT}:8080" \
    -e PORT=8080 \
    --name "evidence-factory-${PORT}" \
    "$IMAGE"

echo ""
echo "Access URL: http://${HOST_IP}:${PORT}"
echo "Local URL:  http://localhost:${PORT}"
echo "View logs:  docker logs evidence-factory-${PORT}"
