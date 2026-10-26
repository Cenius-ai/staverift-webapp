#!/usr/bin/env bash
#
# StaveRift demo walkthrough.
#
# Creates a scratch store, then exercises every command end to end:
# typed job creation, the stave diameter calculator, a live clamp start/stop,
# two backfilled clamp sessions, the human listings, the single-job view with
# its clamped total, the --json modes and the two failure messages.
#
# Nothing here touches your real log: the scratch store lives in
# $STAVERIFT_DEMO_DIR (a temp directory by default) and is printed at the end.
set -euo pipefail
cd "$(dirname "$0")/.."

STORE_DIR="${STAVERIFT_DEMO_DIR:-$(mktemp -d -t staverift-demo-XXXXXX)}"
mkdir -p "$STORE_DIR"
STORE="$STORE_DIR/store.json"
rm -f "$STORE"

PYTHON="${PYTHON:-python3}"
SR=("$PYTHON" staverift.py --store "$STORE")

rule() { printf '\n%s\n' "──────────────────────────────────────────────────────────────────────"; }
step() { rule; printf '%s\n' "$*"; rule; }

# ISO-8601 UTC instant N minutes from now, so backfilled windows read as recent.
stamp() {
  "$PYTHON" -c 'import datetime, sys
now = datetime.datetime.now(datetime.timezone.utc)
print((now + datetime.timedelta(minutes=int(sys.argv[1]))).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$1"
}

step "1. Create typed coopering jobs (closed vocabulary: barrel, cask, firkin, tun)"
BARREL=$("${SR[@]}" jobs create --type barrel --staves 30 --width 90)
printf 'barrel job id: %s\n' "$BARREL"
FIRKIN=$("${SR[@]}" jobs create --type firkin --staves 18 --width 60.5)
printf 'firkin job id: %s\n' "$FIRKIN"
TUN=$("${SR[@]}" jobs create --type tun --staves 20 --width 70)
printf 'tun job id:    %s\n' "$TUN"

step "2. Stave diameter calculator for the 30-stave barrel"
"${SR[@]}" calc --staves 30 --width 90

step "3. Start a live clamp on band 1, hold it, then stop it"
"${SR[@]}" clamp start --job "$BARREL" --band 1
printf 'holding the clamp for a couple of seconds...\n'
sleep 2
"${SR[@]}" clamp stop --job "$BARREL"

step "4. Backfill two clamps that already happened"
"${SR[@]}" clamp backfill --job "$BARREL" --band 2 --start "$(stamp -180)" --end "$(stamp -60)"
"${SR[@]}" clamp backfill --job "$BARREL" --band 3 --start "$(stamp -60)" --end "$(stamp -30)"
"${SR[@]}" clamp backfill --job "$FIRKIN" --band 1 --start "$(stamp -240)" --end "$(stamp -120)"

step "5. List the jobs (id, type, staves, width, created)"
"${SR[@]}" jobs list

step "6. Show the barrel: fields plus total clamped minutes"
"${SR[@]}" jobs show "$BARREL"

step "7. List clamp entries for that job (band, start, end, minutes)"
"${SR[@]}" clamp list --job "$BARREL"

step "8. Machine-readable mode: the same job as one JSON object"
"${SR[@]}" jobs show "$BARREL" --json

step "9. Machine-readable listings (empty results print [] and exit 0)"
"${SR[@]}" clamp list --job "$TUN" --json

step "10. Validation: a missing --band is refused with exit code 2"
set +e
"${SR[@]}" clamp start --job "$BARREL"
BAND_CODE=$?
set -e
printf 'exit code: %s\n' "$BAND_CODE"

step "11. A hand-edit typo is reported with the path and a recovery step"
printf '{ not json' > "$STORE_DIR/broken.json"
set +e
"$PYTHON" staverift.py --store "$STORE_DIR/broken.json" jobs list
BROKEN_CODE=$?
set -e
printf 'exit code: %s\n' "$BROKEN_CODE"

step "Done"
printf 'The whole log is one JSON file you can open in any editor:\n  %s\n' "$STORE"
printf 'Poke at it, then re-run: bash demo/walkthrough.sh\n'
