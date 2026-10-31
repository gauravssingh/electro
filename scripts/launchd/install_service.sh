#!/bin/bash
set -e

# Determine directory of this script and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Target LaunchAgent path
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_NAME="com.gauravs.electro.plist"
TARGET_PLIST="${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"
LOG_DIR="${HOME}/Library/Logs/electro"

# Virtual environment python binary
VENV_PYTHON="${PROJECT_DIR}/.venv/bin/python"

if [ ! -f "${VENV_PYTHON}" ]; then
    echo "Error: Virtual environment python not found at ${VENV_PYTHON}."
    echo "Please create a virtual environment first: python3 -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

# Load environment configuration if available
ELECTRO_HOST="0.0.0.0"
ELECTRO_PORT="5050"
if [ -f "${PROJECT_DIR}/.env" ]; then
    # Extract ELECTRO_HOST and ELECTRO_PORT without executing arbitrary code
    HOST_VAL=$(grep -E '^ELECTRO_HOST=' "${PROJECT_DIR}/.env" | cut -d '=' -f2- | tr -d ' "\047' || true)
    PORT_VAL=$(grep -E '^ELECTRO_PORT=' "${PROJECT_DIR}/.env" | cut -d '=' -f2- | tr -d ' "\047' || true)
    [ -n "${HOST_VAL}" ] && ELECTRO_HOST="${HOST_VAL}"
    [ -n "${PORT_VAL}" ] && ELECTRO_PORT="${PORT_VAL}"
fi

BIND_ADDRESS="${ELECTRO_HOST}:${ELECTRO_PORT}"

# Ensure log directory and LaunchAgents directory exist
mkdir -p "${LOG_DIR}"
mkdir -p "${LAUNCH_AGENTS_DIR}"

echo "Configuring Electro LaunchAgent for user $(whoami)..."
echo "Project directory: ${PROJECT_DIR}"
echo "Python binary:    ${VENV_PYTHON}"
echo "Log directory:    ${LOG_DIR}"
echo "Network binding:  ${BIND_ADDRESS}"

# Generate plist from template with absolute paths and bind address
sed -e "s|{{PYTHON_EXEC}}|${VENV_PYTHON}|g" \
    -e "s|{{PROJECT_DIR}}|${PROJECT_DIR}|g" \
    -e "s|{{LOG_DIR}}|${LOG_DIR}|g" \
    -e "s|{{BIND_ADDRESS}}|${BIND_ADDRESS}|g" \
    "${SCRIPT_DIR}/com.gauravs.electro.plist.template" > "${TARGET_PLIST}"

chmod 644 "${TARGET_PLIST}"
echo "Generated ${TARGET_PLIST}"

# Unload previous instance if loaded
launchctl unload "${TARGET_PLIST}" 2>/dev/null || true
pkill -f "gunicorn.*app:app" 2>/dev/null || true
sleep 1

# Load the service
launchctl load "${TARGET_PLIST}"
echo "Service loaded into launchd."

sleep 2

# Detect LAN IP and mDNS hostname for friendly reporting
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
LOCAL_HOST_NAME=$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || true)

# Check status
if launchctl list | grep -q "com.gauravs.electro"; then
    echo "✅ Electro service is active!"
    echo "Logs are located at: ${LOG_DIR}"
    echo ""
    echo "Accessible at:"
    echo "  • Local Machine: http://127.0.0.1:${ELECTRO_PORT}"
    if [ -n "${LAN_IP}" ]; then
        echo "  • Local Network: http://${LAN_IP}:${ELECTRO_PORT}"
    fi
    if [ -n "${LOCAL_HOST_NAME}" ]; then
        echo "  • mDNS (Bonjour): http://${LOCAL_HOST_NAME}.local:${ELECTRO_PORT}"
    fi
    echo ""
    echo "Health check:        curl http://127.0.0.1:${ELECTRO_PORT}/api/health"
else
    echo "⚠️ Service did not start immediately. Check logs at ${LOG_DIR}/electro.stderr.log"
fi
