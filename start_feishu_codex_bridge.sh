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
export FEISHU_CODEX_BACKEND="${FEISHU_CODEX_BACKEND:-exec}"
export FEISHU_CODEX_MODEL="${FEISHU_CODEX_MODEL:-}"
export FEISHU_TOPIC_IDLE_SECONDS="${FEISHU_TOPIC_IDLE_SECONDS:-7200}"
export FEISHU_TOPIC_NOTICE_ENABLED="${FEISHU_TOPIC_NOTICE_ENABLED:-1}"
export FEISHU_TASK_PROGRESS_SECONDS="${FEISHU_TASK_PROGRESS_SECONDS:-7200}"
export FEISHU_GROUP_AUTO_REPLY_ENABLED="${FEISHU_GROUP_AUTO_REPLY_ENABLED:-1}"
export FEISHU_GROUP_AUTO_REPLY_MAX_HUMAN_MEMBERS="${FEISHU_GROUP_AUTO_REPLY_MAX_HUMAN_MEMBERS:-1}"
export FEISHU_GROUP_AUTO_REPLY_CHAT_IDS="${FEISHU_GROUP_AUTO_REPLY_CHAT_IDS:-}"
export FEISHU_GROUP_MEMBER_CACHE_SECONDS="${FEISHU_GROUP_MEMBER_CACHE_SECONDS:-600}"
export FEISHU_CODEX_CARDS_ENABLED="${FEISHU_CODEX_CARDS_ENABLED:-1}"
export FEISHU_CARDKIT_ENABLED="${FEISHU_CARDKIT_ENABLED:-1}"
export FEISHU_DOCS_ENABLED="${FEISHU_DOCS_ENABLED:-0}"
export FEISHU_DOCS_FOLDER_TOKEN="${FEISHU_DOCS_FOLDER_TOKEN:-}"
export FEISHU_DOCS_DOMAIN="${FEISHU_DOCS_DOMAIN:-https://feishu.cn}"
export FEISHU_DOCS_AUTO_MIN_CHARS="${FEISHU_DOCS_AUTO_MIN_CHARS:-4500}"
export FEISHU_DOCS_BLOCK_CHARS="${FEISHU_DOCS_BLOCK_CHARS:-1500}"
export NODE_BIN="${NODE_BIN:-$(command -v node || detect_nvm_file "${HOME}/.nvm/versions/node/*/bin/node")}"
export CODEX_BIN="${CODEX_BIN:-$(command -v codex || detect_nvm_file "${HOME}/.nvm/versions/node/*/bin/codex")}"
export PYTHON_BIN="${PYTHON_BIN:-$(detect_nvm_file "/Library/Frameworks/Python.framework/Versions/*/bin/python3")}"
export PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"

if [[ -z "${SSL_CERT_FILE:-}" || ! -f "${SSL_CERT_FILE:-}" ]]; then
  CERTIFI_CAFILE="$("$PYTHON_BIN" - <<'PY' 2>/dev/null || true
try:
    import certifi
except Exception:
    raise SystemExit(0)
print(certifi.where())
PY
)"
  if [[ -n "$CERTIFI_CAFILE" && -f "$CERTIFI_CAFILE" ]]; then
    export SSL_CERT_FILE="$CERTIFI_CAFILE"
    export REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-$CERTIFI_CAFILE}"
  fi
fi

exec "$PYTHON_BIN" -u feishu_codex_bridge.py
