#!/usr/bin/env bash
# Remove this stack's containers and volumes.
cd "$(dirname "$0")"
docker compose down -v --remove-orphans
