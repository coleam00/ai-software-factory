#!/usr/bin/env bash
set -eu
# The timer belongs to Factory; the entire agent process belongs to Archon.
interval="${FACTORY_INTERVAL_SECONDS:-300}"
case "$interval" in ''|*[!0-9]*|0) echo "Use a positive FACTORY_INTERVAL_SECONDS" >&2; exit 2;; esac
while [ ! -f .factory/STOP ]; do
  python factory/consumer.py tick || exit "$?"
  sleep "$interval"
done
