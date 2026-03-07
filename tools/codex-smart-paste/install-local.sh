#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$HOME/.vscode/extensions/local.codex-smart-paste-0.0.1"

if [[ -L "$TARGET_DIR" ]]; then
  CURRENT_TARGET="$(readlink "$TARGET_DIR")"
  if [[ "$CURRENT_TARGET" == "$SOURCE_DIR" ]]; then
    echo "Already installed: $TARGET_DIR"
    exit 0
  fi
fi

if [[ -e "$TARGET_DIR" ]]; then
  echo "Refusing to overwrite existing path: $TARGET_DIR" >&2
  exit 1
fi

ln -s "$SOURCE_DIR" "$TARGET_DIR"
echo "Installed: $TARGET_DIR"
echo "Reload VS Code to activate the extension."
