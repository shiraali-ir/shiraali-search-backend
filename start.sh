#!/bin/sh
set -eu

if [ -z "${SEARXNG_SECRET:-}" ]; then
  echo "SEARXNG_SECRET environment variable is required" >&2
  exit 1
fi

export SEARXNG_SECRET_KEY="$SEARXNG_SECRET"
exec /usr/local/searxng/dockerfiles/docker-entrypoint.sh
