#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "${NOIRO_REFRESH_CORE:-0}" = "1" ]; then
  if [ -n "${NOIRO_APPLE_REPO:-}" ]; then
    python3 "$root/scripts/fetch_upstream_core.py" --output "$root/native/stremiox-core" --local-repo "$NOIRO_APPLE_REPO"
  else
    python3 "$root/scripts/fetch_upstream_core.py" --output "$root/native/stremiox-core"
  fi
fi
test -f "$root/native/stremiox-core/NOIRO_SNAPSHOT.json"
docker build -f "$root/docker/Dockerfile.armhf" -t noiro-kodi-armhf "$root"
container=$(docker create noiro-kodi-armhf)
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT HUP INT TERM
destination="$root/addons/script.service.noiro/resources/bin/linux-armhf"
mkdir -p "$destination"
docker cp "$container:/src/native/noiro-engine/target/armv7-unknown-linux-gnueabihf/release/noiro-engine" \
  "$destination/noiro-engine"
chmod 0755 "$destination/noiro-engine"
