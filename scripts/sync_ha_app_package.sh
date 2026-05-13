#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${ROOT_DIR}/addon/app"

mkdir -p "${APP_DIR}"

for file in \
    assistant.py \
    deploy_server.py \
    config.py \
    shared.py \
    skills.py \
    llm_provider.py \
    i18n.py \
    behavior.txt \
    KNOWN_APPLIANCES.json
do
    cp "${ROOT_DIR}/${file}" "${APP_DIR}/${file}"
done

echo "Home Assistant App package synced to addon/app"
