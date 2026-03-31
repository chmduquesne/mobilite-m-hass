#!/usr/bin/env bash
set -euo pipefail

CONTAINER=mobilite-m-hass-dev
CONFIG_DIR="$(pwd)/ha_config"
IMAGE=ghcr.io/home-assistant/home-assistant:stable

case "${1:-}" in
  start)
    mkdir -p "$CONFIG_DIR"
    if podman container exists "$CONTAINER"; then
      echo "Container already exists, starting..."
      podman start "$CONTAINER"
    else
      podman run -d \
        --name "$CONTAINER" \
        -p 8123:8123 \
        -v "$CONFIG_DIR:/config:z" \
        -v "$(pwd)/custom_components:/config/custom_components:z" \
        "$IMAGE"
    fi
    echo "Home Assistant is starting at http://localhost:8123"
    ;;
  stop)
    podman stop "$CONTAINER"
    ;;
  restart)
    podman stop "$CONTAINER" 2>/dev/null || true
    podman start "$CONTAINER"
    echo "Restarted — http://localhost:8123"
    ;;
  logs)
    podman logs -f "$CONTAINER"
    ;;
  clean)
    podman stop "$CONTAINER" 2>/dev/null || true
    podman rm "$CONTAINER"
    rm -rf "$CONFIG_DIR"
    echo "Container and config directory removed."
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|logs|clean}"
    exit 1
    ;;
esac
