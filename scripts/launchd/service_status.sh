#!/bin/bash

LOG_DIR="${HOME}/Library/Logs/electro"

echo "=== LaunchAgent Status ==="
launchctl list | grep "com.gauravs.electro" || echo "Service not found in launchctl list"

echo ""
echo "=== Telemetry Health Probe ==="
curl -s http://127.0.0.1:5000/api/health || echo "Unable to connect to local health endpoint (service may be stopped)"

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
