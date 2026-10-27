#!/bin/sh
set -eu

# Railway mounts volumes as root. Initialize the data path, then drop privileges.
if [ "$(id -u)" = "0" ]; then
    mkdir -p "${COASTWISE_DATA_DIR:-/app/.local/runs}"
    chown -R appuser:appuser "${COASTWISE_DATA_DIR:-/app/.local/runs}"
    exec gosu appuser "$@"
fi
exec "$@"
