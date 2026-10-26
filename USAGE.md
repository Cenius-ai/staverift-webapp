# Using StaveRift

Every command, with the real output it produces. Examples use a scratch store so
they are safe to paste; drop `--store /tmp/shop.json` to work on your real log.

## 1. Create a job

```bash
$ staverift --store /tmp/shop.json jobs create --type barrel --staves 30 --width 90
9c1e4b7a2f0d4e8fa1b2c3d4e5f60718
```

stdout is the new job id and nothing else, so it feeds straight into the next
command. The type must be one of `barrel`, `cask`, `firkin`, `tun`:

```bash
$ staverift jobs create --type foo --staves 30 --width 90
invalid --type 'foo'; allowed: barrel, cask, firkin, tun
$ echo $?
2
```

Each bad flag gets its own line, and the store is left untouched:

```bash
$ staverift jobs create --type cask --staves 2 --width 0
invalid --staves '2'; must be an integer >= 3
invalid --width '0'; must be a number > 0
```

## 2. Check the stave set geometry

```bash
$ staverift calc --staves 30 --width 90
diameter = width / sin(pi / staves); circumference = pi * diameter
FIELD               VALUE
─────────────  ──────────
staves                 30
width            90.00 mm
diameter        861.01 mm
circumference  2704.94 mm
```

`diameter = width / sin(pi / staves)`, `circumference = pi * diameter`. Both are
rounded to two decimals and echoed next to the inputs you supplied, so a
screenshot or a pasted line is self-documenting. `calc` never reads the store.

## 3. Time a clamp

Open a session (band required, integer >= 1), then close it:

```bash
$ staverift clamp start --job 9c1e4b7a2f0d4e8fa1b2c3d4e5f60718 --band 1
started band 1 on job 9c1e4b7a2f0d4e8fa1b2c3d4e5f60718 at 2024-05-01T08:12:00Z
$ staverift clamp stop --job 9c1e4b7a2f0d4e8fa1b2c3d4e5f60718
stopped band 1 on job 9c1e4b7a2f0d4e8fa1b2c3d4e5f60718 at 2024-05-01T09:42:00Z (90.0 minutes)
```

Only one session may be open per job at a time:

```bash
$ staverift clamp start --job 9c1e4b7a… --band 2
job 9c1e4b7a2f0d4e8fa1b2c3d4e5f60718 already has an open clamp on band 1
```

## 4. Backfill a clamp you already did

```bash
$ staverift clamp backfill --job 9c1e4b7a… --band 2 \
    --start 2024-05-01T08:00:00Z --end 2024-05-01T10:30:00Z
backfilled band 2 on job 9c1e4b7a2f0d4e8fa1b2c3d4e5f60718: 150.0 minutes (2024-05-01T08:00:00Z → 2024-05-01T10:30:00Z)
```

The end must be after the start:

```bash
$ staverift clamp backfill --job 9c1e4b7a… --band 2 \
    --start 2024-05-01T10:00:00Z --end 2024-05-01T09:00:00Z
invalid --end '2024-05-01T09:00:00Z'; must be after --start
```

## 5. Review the log

```bash
$ staverift jobs list
3 jobs · widths in mm
ID                                TYPE    STAVES  WIDTH  CREATED
────────────────────────────────  ──────  ──────  ─────  ────────────────────
9c1e4b7a2f0d4e8fa1b2c3d4e5f60718  barrel      30  90.00  2024-04-28T07:40:00Z
7f2c5d0a19b84e3f8a6c1d2e3f405162  firkin      18  60.50  2024-04-29T09:05:00Z
1a2b3c4d5e6f70819a2b3c4d5e6f7081  tun         20  70.00  2024-04-30T06:20:00Z
```

```bash
$ staverift jobs show 9c1e4b7a
job 9c1e4b7a2f0d4e8fa1b2c3d4e5f60718
  type:      barrel
  staves:    30
  width:     90.00 mm
  created:   2024-04-28T07:40:00Z
  clamps:    2
total clamped minutes: 240.0
```

```bash
$ staverift clamp list --job 9c1e4b7a
2 clamp entries
BAND  JOB           START                 END                   MINUTES
────  ────────────  ────────────────────  ────────────────────  ───────
   2  9c1e4b7a2f0d  2024-05-01T08:00:00Z  2024-05-01T10:30:00Z    150.0
   1  9c1e4b7a2f0d  2024-05-01T08:12:00Z  2024-05-01T09:42:00Z     90.0
```

`--job` accepts a unique id prefix. An open session shows `open` in the END
column and `-` for minutes until it is stopped.

Empty stores say what to do next, and exit 0:

```bash
$ staverift jobs list
no jobs yet; create one with: staverift jobs create --type barrel --staves 30 --width 90
$ staverift clamp list
no clamp entries yet; start one with: staverift clamp start --job <id> --band 1
```

## 6. Script it

`--json` prints one JSON document on stdout and nothing else — diagnostics stay
on stderr. Zero matches print `[]` and exit 0, so pipelines never break.

```bash
$ staverift jobs show 9c1e4b7a --json
{
  "created_at": "2024-04-28T07:40:00Z",
  "id": "9c1e4b7a2f0d4e8fa1b2c3d4e5f60718",
  "staves": 30,
  "total_clamped_minutes": 240.0,
  "type": "barrel",
  "width": 90.0
}
$ staverift clamp list --job 1a2b3c4d --json
[]
```

A job with no closed clamp entries reports `"total_clamped_minutes": 0`.

## 7. Fixing the store by hand

The log is one JSON file (`~/.staverift/store.json` unless overridden), indented
with two spaces and sorted keys. Edit it with any editor; the next command reads
it. If a hand edit breaks the JSON you get the path and the fix, not a
traceback:

```bash
$ staverift jobs list
Cannot read store at /home/cooper/.staverift/store.json: Expecting property name enclosed in double quotes at line 4 column 5. Fix or delete the file to start fresh.
$ echo $?
1
```
