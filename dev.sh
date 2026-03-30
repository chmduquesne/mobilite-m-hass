#!/usr/bin/env bash
set -euo pipefail

CONTAINER=mobilite-m-hass-dev
VOLUME=ha_config
IMAGE=ghcr.io/home-assistant/home-assistant:stable

case "${1:-}" in
  start)
    podman volume exists "$VOLUME" || podman volume create "$VOLUME"
    if podman container exists "$CONTAINER"; then
      echo "Container already exists, starting..."
      podman start "$CONTAINER"
    else
      podman run -d \
        --name "$CONTAINER" \
        -p 8123:8123 \
        -v "$(pwd)/custom_components:/config/custom_components:z" \
        -v "$VOLUME:/config" \
        "$IMAGE"
    fi
    echo "Home Assistant is starting at http://localhost:8123"
    ;;
  stop)
    podman stop "$CONTAINER"
    ;;
  restart)
    if podman container inspect "$CONTAINER" --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
      podman restart "$CONTAINER"
    else
      podman start "$CONTAINER"
    fi
    echo "Restarted — http://localhost:8123"
    ;;
  logs)
    podman logs -f "$CONTAINER"
    ;;
  clean)
    podman stop "$CONTAINER" 2>/dev/null || true
    podman rm "$CONTAINER"
    podman volume rm "$VOLUME"
    echo "Container and volume removed."
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|logs|clean}"
    exit 1
    ;;
esac
