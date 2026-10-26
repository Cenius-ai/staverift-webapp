#!/usr/bin/env bash
#
# StaveRift setup: install project-level dependencies, install the `staverift`
# console script, verify the import, then EXIT. It never starts a server or a
# background process, never uses sudo, and is safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: $PYTHON not found; install Python 3.9+ first (see INSTALL.md)" >&2
  exit 1
fi

echo "==> python: $("$PYTHON" --version 2>&1)"
echo "==> upgrading packaging tools"
"$PYTHON" -m pip install --upgrade pip setuptools wheel

echo "==> installing pinned dependencies from requirements.txt"
"$PYTHON" -m pip install -r requirements.txt

echo "==> installing the staverift console script (editable, no build isolation)"
"$PYTHON" -m pip install --no-build-isolation -e .

echo "==> import self-check"
"$PYTHON" -c 'import staverift; print("staverift", staverift.__version__, "imports cleanly")'

cat <<'DONE'

Setup complete. Nothing is running in the background.

  staverift --help            # installed console script
  python3 staverift.py --help # same thing straight from the checkout
  bash demo.sh                # full walkthrough against a scratch store
  python3 -m pytest -q        # test suite
DONE
