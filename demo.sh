#!/usr/bin/env bash
#
# One-command StaveRift demo: runs the full walkthrough non-interactively
# against a scratch store (never your real ~/.staverift/store.json).
set -euo pipefail
cd "$(dirname "$0")"
exec bash demo/walkthrough.sh "$@"
