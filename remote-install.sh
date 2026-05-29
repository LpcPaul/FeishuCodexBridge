#!/usr/bin/env bash
set -euo pipefail

REPO_TARBALL_URL="${FEISHU_CODEX_BRIDGE_TARBALL_URL:-https://github.com/LpcPaul/FeishuCodexBridge/archive/refs/heads/main.tar.gz}"
TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

curl -fsSL "$REPO_TARBALL_URL" | tar -xz -C "$TMP_DIR"
PROJECT_DIR="$(find "$TMP_DIR" -maxdepth 1 -type d -name 'FeishuCodexBridge-*' | head -n 1)"

if [[ -z "$PROJECT_DIR" ]]; then
  echo "Failed to unpack FeishuCodexBridge." >&2
  exit 1
fi

cd "$PROJECT_DIR"
exec ./install.sh
