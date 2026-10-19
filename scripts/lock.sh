#!/usr/bin/env bash
# Regenerate the hash-locked requirement files after changing dependencies in
# pyproject.toml. The locks are universal: one file serves Linux and macOS on
# Python 3.12-3.14. Every install path (Docker, CI, the VM script) installs
# from these with --require-hashes, so an unrecorded package cannot get in.
set -euo pipefail
command -v uv >/dev/null || { echo "needs uv: https://docs.astral.sh/uv/" >&2; exit 1; }
cd "$(dirname "$0")/.."
opts=(--universal --python-version 3.12 --generate-hashes --no-header -q)
uv pip compile pyproject.toml --extra postgres "${opts[@]}" -o requirements.lock
uv pip compile pyproject.toml --extra postgres --extra dev "${opts[@]}" -o requirements-dev.lock
printf 'hatchling\neditables\n' | uv pip compile - "${opts[@]}" -o requirements-build.lock
echo "locked: $(grep -c '^[a-z]' requirements.lock) runtime, $(grep -c '^[a-z]' requirements-dev.lock) dev, $(grep -c '^[a-z]' requirements-build.lock) build"
