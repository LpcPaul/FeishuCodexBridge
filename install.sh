#!/usr/bin/env bash
set -euo pipefail

LABEL="com.codex.feishu-codex-bridge"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${FEISHU_CODEX_RUNTIME_DIR:-${HOME}/Library/Application Support/FeishuCodexBridge}"
APP_DIR="${RUNTIME_DIR}/app"
ENV_FILE="${APP_DIR}/.env.feishu"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer currently supports macOS launchd only." >&2
  exit 1
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "python3 is required. Install Python 3 and run this installer again." >&2
  exit 1
fi

detect_file() {
  local pattern="$1"
  ls -1d $pattern 2>/dev/null | sort | tail -n 1 || true
}

detect_codex() {
  command -v codex 2>/dev/null || detect_file "${HOME}/.nvm/versions/node/*/bin/codex"
}

detect_node() {
  command -v node 2>/dev/null || detect_file "${HOME}/.nvm/versions/node/*/bin/node"
}

prompt_secret() {
  local label="$1"
  local current="${2:-}"
  if [[ -n "$current" ]]; then
    printf '%s' "$current"
    return
  fi
  read -r -s -p "$label: " current
  echo >&2
  printf '%s' "$current"
}

prompt_value() {
  local label="$1"
  local current="${2:-}"
  if [[ -n "$current" ]]; then
    printf '%s' "$current"
    return
  fi
  read -r -p "$label: " current
  printf '%s' "$current"
}

cat <<'NOTICE'
Before installing, make sure the Feishu app has these initial permissions/events:
  im:message.p2p_msg:readonly
  im:message.group_at_msg:readonly
  im:message:send_as_bot
  im.message.receive_v1
Recommended if you want cards, docs, or group creation later:
  card.action.trigger
  cardkit:card:write
  docx:document or docx:document:create
  im:chat:create
  im:chat
See docs/initial-permissions.md for the import JSON.

NOTICE

FEISHU_APP_ID="$(prompt_value "Feishu App ID" "${FEISHU_APP_ID:-}")"
FEISHU_APP_SECRET="$(prompt_secret "Feishu App Secret" "${FEISHU_APP_SECRET:-}")"

if [[ -z "$FEISHU_APP_ID" || -z "$FEISHU_APP_SECRET" ]]; then
  echo "FEISHU_APP_ID and FEISHU_APP_SECRET are required." >&2
  exit 1
fi

NODE_BIN="${NODE_BIN:-$(detect_node)}"
CODEX_BIN="${CODEX_BIN:-$(detect_codex)}"
FEISHU_CODEX_WORKDIR="${FEISHU_CODEX_WORKDIR:-${HOME}}"

if [[ -z "$CODEX_BIN" ]]; then
  echo "Codex CLI was not found. Install and log in to Codex first, then rerun install.sh." >&2
  exit 1
fi

mkdir -p "$APP_DIR" "$(dirname "$PLIST_PATH")"

if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude ".git/" \
    --exclude "__pycache__/" \
    --exclude ".env.feishu" \
    --exclude "*.sqlite" \
    --exclude "*.sqlite-*" \
    "$PROJECT_DIR/" "$APP_DIR/"
else
  cp -R "$PROJECT_DIR"/. "$APP_DIR/"
  rm -rf "$APP_DIR/.git" "$APP_DIR/__pycache__"
fi

if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import lark_oapi
PY
then
  "$PYTHON_BIN" -m pip install --user -r "$APP_DIR/requirements.txt"
fi

export FEISHU_APP_ID FEISHU_APP_SECRET FEISHU_CODEX_WORKDIR RUNTIME_DIR PYTHON_BIN NODE_BIN CODEX_BIN
"$PYTHON_BIN" - "$ENV_FILE" <<'PY'
import os
import shlex
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
values = {
    "FEISHU_APP_ID": os.environ["FEISHU_APP_ID"],
    "FEISHU_APP_SECRET": os.environ["FEISHU_APP_SECRET"],
    "FEISHU_CODEX_WORKDIR": os.environ["FEISHU_CODEX_WORKDIR"],
    "FEISHU_CODEX_RUNTIME_DIR": os.environ["RUNTIME_DIR"],
    "PYTHON_BIN": os.environ["PYTHON_BIN"],
    "NODE_BIN": os.environ.get("NODE_BIN", ""),
    "CODEX_BIN": os.environ["CODEX_BIN"],
    "FEISHU_TOPIC_IDLE_SECONDS": os.environ.get("FEISHU_TOPIC_IDLE_SECONDS", "7200"),
    "FEISHU_TOPIC_NOTICE_ENABLED": os.environ.get("FEISHU_TOPIC_NOTICE_ENABLED", "1"),
    "FEISHU_TOPIC_NOTICE_POLL_SECONDS": os.environ.get("FEISHU_TOPIC_NOTICE_POLL_SECONDS", "60"),
    "FEISHU_TASK_PROGRESS_SECONDS": os.environ.get("FEISHU_TASK_PROGRESS_SECONDS", "7200"),
    "FEISHU_ACK_TEXT": os.environ.get("FEISHU_ACK_TEXT", "收到，我要开始干活了，稍等我"),
    "FEISHU_GROUP_AUTO_REPLY_ENABLED": os.environ.get("FEISHU_GROUP_AUTO_REPLY_ENABLED", "1"),
    "FEISHU_GROUP_AUTO_REPLY_MAX_HUMAN_MEMBERS": os.environ.get("FEISHU_GROUP_AUTO_REPLY_MAX_HUMAN_MEMBERS", "1"),
    "FEISHU_GROUP_AUTO_REPLY_CHAT_IDS": os.environ.get("FEISHU_GROUP_AUTO_REPLY_CHAT_IDS", ""),
    "FEISHU_GROUP_MEMBER_CACHE_SECONDS": os.environ.get("FEISHU_GROUP_MEMBER_CACHE_SECONDS", "600"),
    "FEISHU_CODEX_CARDS_ENABLED": os.environ.get("FEISHU_CODEX_CARDS_ENABLED", "1"),
    "FEISHU_CARDKIT_ENABLED": os.environ.get("FEISHU_CARDKIT_ENABLED", "0"),
    "FEISHU_DOCS_ENABLED": os.environ.get("FEISHU_DOCS_ENABLED", "0"),
    "FEISHU_DOCS_FOLDER_TOKEN": os.environ.get("FEISHU_DOCS_FOLDER_TOKEN", ""),
    "FEISHU_DOCS_DOMAIN": os.environ.get("FEISHU_DOCS_DOMAIN", "https://feishu.cn"),
    "FEISHU_DOCS_AUTO_MIN_CHARS": os.environ.get("FEISHU_DOCS_AUTO_MIN_CHARS", "4500"),
    "FEISHU_DOCS_BLOCK_CHARS": os.environ.get("FEISHU_DOCS_BLOCK_CHARS", "1500"),
}
env_path.write_text(
    "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items()) + "\n",
    encoding="utf-8",
)
PY
chmod 600 "$ENV_FILE"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${APP_DIR}/start_feishu_codex_bridge.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${APP_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${RUNTIME_DIR}/feishu_codex_bridge.log</string>
  <key>StandardErrorPath</key>
  <string>${RUNTIME_DIR}/feishu_codex_bridge.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

echo "FeishuCodexBridge installed."
echo "Service: ${LABEL}"
echo "Runtime: ${RUNTIME_DIR}"
echo "Logs: ${RUNTIME_DIR}/feishu_codex_bridge.log"
