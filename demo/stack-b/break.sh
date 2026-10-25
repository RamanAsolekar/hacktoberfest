#!/usr/bin/env bash
# Deploy this stack with the app's database password missing (the demo failure).
set -e
cd "$(dirname "$0")"
cp env.demo .env
cp compose.broken.yaml compose.yaml
rm -f compose.yaml.dockergpt.bak
docker compose up -d --build
echo "$(basename "$PWD") deployed with a missing DB password; watch it crash-loop."
