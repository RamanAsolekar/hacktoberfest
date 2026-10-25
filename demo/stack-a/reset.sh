#!/usr/bin/env bash
# Put this stack back in its working state.
set -e
cd "$(dirname "$0")"
cp env.demo .env
cp compose.good.yaml compose.yaml
rm -f compose.yaml.dockergpt.bak
docker compose up -d --build
echo "$(basename "$PWD") reset to a working state."
