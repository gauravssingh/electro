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

# Ensure log directory and LaunchAgents directory exist
mkdir -p "${LOG_DIR}"
mkdir -p "${LAUNCH_AGENTS_DIR}"

echo "Configuring Electro LaunchAgent for user $(whoami)..."
echo "Project directory: ${PROJECT_DIR}"
echo "Python binary:    ${VENV_PYTHON}"
echo "Log directory:    ${LOG_DIR}"

# Generate plist from template with absolute paths
sed -e "s|{{PYTHON_EXEC}}|${VENV_PYTHON}|g" \
    -e "s|{{PROJECT_DIR}}|${PROJECT_DIR}|g" \
    -e "s|{{LOG_DIR}}|${LOG_DIR}|g" \
    "${SCRIPT_DIR}/com.gauravs.electro.plist.template" > "${TARGET_PLIST}"

chmod 644 "${TARGET_PLIST}"
echo "Generated ${TARGET_PLIST}"

# Unload previous instance if loaded
launchctl unload "${TARGET_PLIST}" 2>/dev/null || true

# Load the service
launchctl load "${TARGET_PLIST}"
echo "Service loaded into launchd."

sleep 2

# Check status
if launchctl list | grep -q "com.gauravs.electro"; then
    echo "✅ Electro service is active!"
    echo "Logs are located at: ${LOG_DIR}"
    echo "Dashboard URL:      http://127.0.0.1:5000"
    echo "Health check:       curl http://127.0.0.1:5000/api/health"
else
    echo "⚠️ Service did not start immediately. Check logs at ${LOG_DIR}/electro.stderr.log"
fi
