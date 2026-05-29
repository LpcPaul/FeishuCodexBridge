#!/usr/bin/env bash
set -euo pipefail

LABEL="com.codex.feishu-codex-bridge"
RUNTIME_DIR="${FEISHU_CODEX_RUNTIME_DIR:-${HOME}/Library/Application Support/FeishuCodexBridge}"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"

if [[ "${REMOVE_FEISHU_CODEX_BRIDGE_DATA:-0}" == "1" ]]; then
  rm -rf "$RUNTIME_DIR"
  echo "FeishuCodexBridge service and runtime data removed."
else
  echo "FeishuCodexBridge service removed. Runtime data kept at: $RUNTIME_DIR"
  echo "Set REMOVE_FEISHU_CODEX_BRIDGE_DATA=1 to delete runtime data during uninstall."
fi
