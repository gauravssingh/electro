#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LOG_DIR="${HOME}/Library/Logs/electro"

ELECTRO_PORT="5050"
if [ -f "${PROJECT_DIR}/.env" ]; then
    PORT_VAL=$(grep -E '^ELECTRO_PORT=' "${PROJECT_DIR}/.env" | cut -d '=' -f2- | tr -d ' "\047' || true)
    [ -n "${PORT_VAL}" ] && ELECTRO_PORT="${PORT_VAL}"
fi

echo "=== LaunchAgent Status ==="
launchctl list | grep "com.gauravs.electro" || echo "Service not found in launchctl list"

echo ""
echo "=== Telemetry Health Probe ==="
curl -s "http://127.0.0.1:${ELECTRO_PORT}/api/health" || echo "Unable to connect to local health endpoint (service may be stopped)"

echo ""
echo "=== Recent stdout (last 10 lines) ==="
if [ -f "${LOG_DIR}/electro.stdout.log" ]; then
    tail -n 10 "${LOG_DIR}/electro.stdout.log"
else
    echo "No stdout log found at ${LOG_DIR}/electro.stdout.log"
fi

echo ""
echo "=== Recent stderr (last 10 lines) ==="
if [ -f "${LOG_DIR}/electro.stderr.log" ]; then
    tail -n 10 "${LOG_DIR}/electro.stderr.log"
else
    echo "No stderr log found at ${LOG_DIR}/electro.stderr.log"
fi
