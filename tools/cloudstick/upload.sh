#!/bin/sh
# Only published batches. No mounts, live-image reads, direct rclone copy, or deletion.
set -eu
if [ "$#" -ne 1 ]; then
    echo 'Usage: upload.sh /absolute/path/to/private-worker.json' >&2
    exit 2
fi
case "$1" in
    /*) ;;
    *) echo 'An absolute worker configuration path is required' >&2; exit 2 ;;
esac
exec /opt/raspi-cloud-sync-pro/current/venv/bin/raspi-cloud-sync-pro backup-staged --config "$1" --once
