#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_RUNTIME_DIR="${HOME}/Library/Application Support/FeishuCodexBridge"
FEISHU_ENV_FILE="${FEISHU_ENV_FILE:-${PROJECT_DIR}/.env.feishu}"

cd "$PROJECT_DIR"

if [[ ! -f "$FEISHU_ENV_FILE" ]]; then
  echo "Missing Feishu env file: $FEISHU_ENV_FILE" >&2
  exit 1
fi

set -a
source "$FEISHU_ENV_FILE"
set +a

detect_nvm_file() {
  local pattern="$1"
  local result=""
  result="$(ls -1d $pattern 2>/dev/null | sort | tail -n 1 || true)"
  printf '%s' "$result"
}

export FEISHU_CODEX_WORKDIR="${FEISHU_CODEX_WORKDIR:-${HOME}}"
export FEISHU_CODEX_RUNTIME_DIR="${FEISHU_CODEX_RUNTIME_DIR:-${DEFAULT_RUNTIME_DIR}}"
export FEISHU_TOPIC_IDLE_SECONDS="${FEISHU_TOPIC_IDLE_SECONDS:-7200}"
export FEISHU_TOPIC_NOTICE_ENABLED="${FEISHU_TOPIC_NOTICE_ENABLED:-1}"
export FEISHU_TASK_PROGRESS_SECONDS="${FEISHU_TASK_PROGRESS_SECONDS:-7200}"
export NODE_BIN="${NODE_BIN:-$(command -v node || detect_nvm_file "${HOME}/.nvm/versions/node/*/bin/node")}"
export CODEX_BIN="${CODEX_BIN:-$(command -v codex || detect_nvm_file "${HOME}/.nvm/versions/node/*/bin/codex")}"
export PYTHON_BIN="${PYTHON_BIN:-$(detect_nvm_file "/Library/Frameworks/Python.framework/Versions/*/bin/python3")}"
export PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"

exec "$PYTHON_BIN" -u feishu_codex_bridge.py
