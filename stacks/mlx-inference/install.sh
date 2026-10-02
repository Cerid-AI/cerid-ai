#!/usr/bin/env bash
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# Install or update the cerid-mlx model server on an Apple-silicon Mac.
#
#   stacks/mlx-inference/install.sh                 # pick a catalog by memory, install, start, check
#   stacks/mlx-inference/install.sh --write-env .env  # also add the unset Cerid settings to .env
#   stacks/mlx-inference/install.sh --help          # every option
#   stacks/mlx-inference/install.sh --uninstall     # stop and remove the agent; weights stay
#
# This script builds the venv and copies the server files into the install
# directory (default ~/.local/share/cerid-mlx); install.py does the rest.
set -euo pipefail

STACK="$(cd "$(dirname "$0")" && pwd)"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "install.sh: cerid-mlx needs an Apple-silicon Mac (MLX runs on Metal). On other hosts use Ollama." >&2
  exit 1
fi

home_dir="${CERID_MLX_HOME:-$HOME/.local/share/cerid-mlx}"
prev=""
for arg in "$@"; do
  case "$arg" in
    --home=*) home_dir="${arg#--home=}" ;;
    -h|--help|--uninstall) exec python3 "$STACK/install.py" "$@" ;;
  esac
  [ "$prev" = "--home" ] && home_dir="$arg"
  prev="$arg"
done
home_dir="${home_dir/#\~/$HOME}"
venv="$home_dir/.venv"

mkdir -p "$home_dir"
if [ ! -x "$venv/bin/python" ]; then
  echo "Creating $venv (Python 3.12)"
  if command -v uv >/dev/null 2>&1; then
    uv venv --quiet --python 3.12 "$venv"
  elif command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv "$venv"
  else
    echo "install.sh: needs Python 3.12. Install uv (https://docs.astral.sh/uv/) or run: brew install python@3.12" >&2
    exit 1
  fi
fi
if ! "$venv/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
  echo "install.sh: $venv is not Python 3.12; remove it and run again." >&2
  exit 1
fi

echo "Installing the pinned packages (requirements.txt)"
if command -v uv >/dev/null 2>&1; then
  uv pip sync --quiet --python "$venv/bin/python" "$STACK/requirements.txt"
else
  "$venv/bin/python" -m pip install --quiet --upgrade pip
  "$venv/bin/python" -m pip install --quiet -r "$STACK/requirements.txt"
fi

for f in serve.py nomic_bert.py check.py; do
  install -m 0644 "$STACK/$f" "$home_dir/$f"
done
mkdir -p "$home_dir/reference"
install -m 0644 "$STACK"/reference/*.json "$home_dir/reference/"

exec "$venv/bin/python" "$STACK/install.py" "$@"
