#!/bin/bash
set -e

LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
PLIST_NAME="com.gauravs.electro.plist"
TARGET_PLIST="${LAUNCH_AGENTS_DIR}/${PLIST_NAME}"

echo "Stopping and uninstalling Electro LaunchAgent..."

if [ -f "${TARGET_PLIST}" ]; then
    launchctl unload "${TARGET_PLIST}" 2>/dev/null || true
    rm -f "${TARGET_PLIST}"
    echo "Removed ${TARGET_PLIST}"
fi

echo "✅ Electro service uninstalled successfully."
