#!/bin/sh
set -eu

# Railway mounts volumes as root. Initialize the data path, then drop privileges.
if [ "$(id -u)" = "0" ]; then
    mkdir -p "${COASTWISE_DATA_DIR:-/app/.local/runs}"
    chown -R appuser:appuser "${COASTWISE_DATA_DIR:-/app/.local/runs}"
    # Client password hash (client_access.py) lives beside the runs directory, outside the release-managed tree.
    data_dir="${COASTWISE_DATA_DIR:-/app/.local/runs}"
    access_dir="$(dirname "${COASTWISE_CLIENT_ACCESS_FILE:-$(dirname "$data_dir")/client-access/client-access.json}")"
    mkdir -p "$access_dir"
    chown appuser:appuser "$access_dir"
    chmod 700 "$access_dir"
    exec gosu appuser "$0" "$@"
fi
uv run --no-sync python -m coastwise.method_a_release --data-root "${COASTWISE_DATA_DIR:-/app/.local/runs}"
exec "$@"
