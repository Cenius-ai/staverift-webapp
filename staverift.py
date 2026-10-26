#!/usr/bin/env python3
"""StaveRift: typed coopering jobs, stave geometry and clamp time logs.

Single-file, standard-library-only command line tool for small-batch coopers.
Jobs and clamp entries live in one hand-editable JSON document.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__version__ = "1.0.0"

SCHEMA_VERSION = 1
JOB_TYPES: Tuple[str, ...] = ("barrel", "cask", "firkin", "tun")
ALLOWED_TYPES = ", ".join(JOB_TYPES)
DEFAULT_STORE = "~/.staverift/store.json"
STORE_ENV_VAR = "STAVERIFT_STORE"

EXIT_OK = 0
EXIT_STORE = 1
EXIT_USAGE = 2

INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
ACCENT_RGB = (16, 144, 103)  # oklch(0.58 0.12 164) == #109067, the project accent


class CliError(Exception):
    """A user-facing failure: one message per line, no traceback."""

    def __init__(self, *messages: str, code: int = EXIT_STORE) -> None:
        super().__init__(messages[0] if messages else "")
        self.messages = list(messages)
        self.code = code


class FlagErrors:
    """Collects one message per offending flag so every problem is reported."""

    def __init__(self) -> None:
        self.messages: List[str] = []

    def add(self, message: str) -> None:
        self.messages.append(message)

    def raise_if_any(self) -> None:
        if self.messages:
            raise CliError(*self.messages, code=EXIT_USAGE)


# --------------------------------------------------------------------------- #
# Terminal presentation
# --------------------------------------------------------------------------- #

def _stdout_supports(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def use_color() -> bool:
    """Colour only when a human is watching and has not opted out."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    stream = sys.stdout
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def accent(text: str) -> str:
    if not use_color():
        return text
    red, green, blue = ACCENT_RGB
    return f"\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m"


def dim(text: str) -> str:
    if not use_color():
        return text
    return f"\x1b[2m{text}\x1b[0m"


def rule_char() -> str:
    return "\u2500" if _stdout_supports("\u2500") else "-"


def arrow() -> str:
    return "\u2192" if _stdout_supports("\u2192") else "->"


def emit(line: str = "", stream: Any = None) -> None:
    print(line, file=stream if stream is not None else sys.stdout, flush=True)


def emit_hint(message: str) -> None:
    """Human asides belong on stderr so stdout stays pipeable."""
    print(dim(message), file=sys.stderr, flush=True)


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    alignments: Sequence[str],
) -> List[str]:
    """Aligned terminal columns with a box-drawing separator under the header."""
    widths = []
    for index, header in enumerate(headers):
        cells = [len(str(header))] + [len(str(row[index])) for row in rows]
        widths.append(max(cells))

    def layout(cells: Sequence[str]) -> str:
        padded = []
        for index, cell in enumerate(cells):
            text = str(cell)
            padded.append(text.rjust(widths[index]) if alignments[index] == "right" else text.ljust(widths[index]))
        return "  ".join(padded).rstrip()

    lines = [layout(list(headers)), "  ".join(rule_char() * width for width in widths)]
    lines.extend(layout(list(row)) for row in rows)
    return lines


def format_minutes(value: Any) -> str:
    """150.0 -> '150.0'; 0.03 -> '0.03'; keeps the decimal the plan prints."""
    if value is None:
        return "-"
    number = float(value)
    if number == int(number):
        return f"{number:.1f}"
    return f"{number:.2f}"


def format_width(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

def resolve_store_path(flag_value: Optional[str]) -> Path:
    """Flags beat the environment, which beats the default location."""
    if flag_value:
        return Path(os.path.expanduser(str(flag_value)))
    from_env = os.environ.get(STORE_ENV_VAR, "").strip()
    if from_env:
        return Path(os.path.expanduser(from_env))
    return Path(os.path.expanduser(DEFAULT_STORE))


def empty_store() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "jobs": [], "clamps": []}


def store_error_message(path: Path, reason: str) -> str:
    return f"Cannot read store at {path}: {reason}. Fix or delete the file to start fresh."


def load_store(path: Path) -> Dict[str, Any]:
    """A missing file is an empty store; an unreadable one is a clean failure."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_store()
    except IsADirectoryError:
        raise CliError(store_error_message(path, "path is a directory"))
    except OSError as error:
        raise CliError(store_error_message(path, error.strerror or str(error)))

    if not raw.strip():
        raise CliError(store_error_message(path, "file is empty"))

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        reason = f"{error.msg} at line {error.lineno} column {error.colno}"
        raise CliError(store_error_message(path, reason))

    if not isinstance(document, dict):
        raise CliError(store_error_message(path, "top-level value must be a JSON object"))

    jobs = document.get("jobs", [])
    clamps = document.get("clamps", [])
    if not isinstance(jobs, list) or not isinstance(clamps, list):
        raise CliError(store_error_message(path, "'jobs' and 'clamps' must be JSON arrays"))
    for entry in jobs:
        if not isinstance(entry, dict) or "id" not in entry:
            raise CliError(store_error_message(path, "every job needs an 'id' field"))
    for entry in clamps:
        if not isinstance(entry, dict) or "id" not in entry:
            raise CliError(store_error_message(path, "every clamp entry needs an 'id' field"))

    return {"schema_version": SCHEMA_VERSION, "jobs": list(jobs), "clamps": list(clamps)}


def save_store(path: Path, document: Dict[str, Any]) -> None:
    """Write a temp file in the same directory, then replace atomically."""
    payload = dict(document)
    payload["schema_version"] = SCHEMA_VERSION
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    directory = path.parent if str(path.parent) else Path(".")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise CliError(f"Cannot write store at {path}: {error.strerror or error}.")

    try:
        handle_fd, temp_name = tempfile.mkstemp(prefix=".staverift-", suffix=".tmp", dir=str(directory))
    except OSError as error:
        raise CliError(f"Cannot write store at {path}: {error.strerror or error}.")

    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except OSError as error:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise CliError(f"Cannot write store at {path}: {error.strerror or error}.")


# --------------------------------------------------------------------------- #
# Domain helpers
# --------------------------------------------------------------------------- #

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(raw: str) -> Optional[datetime]:
    text = str(raw).strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def find_job(store: Dict[str, Any], identifier: str) -> Dict[str, Any]:
    """Exact id first, then a unique prefix so long ids can be shortened."""
    jobs = store["jobs"]
    wanted = str(identifier)
    for job in jobs:
        if job.get("id") == wanted:
            return job
    if len(wanted) >= 4:
        matches = [job for job in jobs if str(job.get("id", "")).startswith(wanted)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise CliError(
                f"ambiguous job id '{wanted}'; matches {len(matches)} jobs. Use more characters.",
                code=EXIT_STORE,
            )
    raise CliError(f"no job with id '{wanted}'", code=EXIT_STORE)


def job_clamps(store: Dict[str, Any], job_id: str) -> List[Dict[str, Any]]:
    return [entry for entry in store["clamps"] if entry.get("job_id") == job_id]


def total_clamped_minutes(store: Dict[str, Any], job_id: str) -> float:
    """Only closed sessions count; an open clamp has no duration yet."""
    total = 0.0
    for entry in job_clamps(store, job_id):
        minutes = entry.get("minutes")
        if minutes is None:
            continue
        total += float(minutes)
    return round(total, 2) if total else 0


def stave_geometry(staves: int, width: float) -> Tuple[float, float]:
    """Closed-form chord relation for a regular stave polygon."""
    diameter = width / math.sin(math.pi / staves)
    circumference = math.pi * diameter
    return round(diameter, 2), round(circumference, 2)


# --------------------------------------------------------------------------- #
# Validation (one message per offending flag, always naming value and bound)
# --------------------------------------------------------------------------- #

def validate_job_type(raw: Optional[str], errors: FlagErrors) -> Optional[str]:
    if raw is None:
        errors.add(f"missing required --type (one of: {ALLOWED_TYPES})")
        return None
    candidate = str(raw).strip().lower()
    if candidate not in JOB_TYPES:
        errors.add(f"invalid --type '{raw}'; allowed: {ALLOWED_TYPES}")
        return None
    return candidate


def validate_integer(raw: Optional[str], flag: str, minimum: int, errors: FlagErrors) -> Optional[int]:
    bound = f"integer >= {minimum}"
    if raw is None:
        errors.add(f"missing required {flag} ({bound})")
        return None
    text = str(raw).strip()
    value: Optional[int]
    try:
        value = int(text, 10) if INTEGER_PATTERN.match(text) else None
    except ValueError:  # pragma: no cover - regex already rejects these
        value = None
    if value is None or value < minimum:
        errors.add(f"invalid {flag} '{raw}'; must be an {bound}")
        return None
    return value


def validate_width(raw: Optional[str], errors: FlagErrors) -> Optional[float]:
    if raw is None:
        errors.add("missing required --width (number > 0)")
        return None
    text = str(raw).strip()
    try:
        value = float(text)
    except ValueError:
        errors.add(f"invalid --width '{raw}'; must be a number > 0")
        return None
    if not math.isfinite(value) or value <= 0:
        errors.add(f"invalid --width '{raw}'; must be a number > 0")
        return None
    return value


def validate_timestamp(
    raw: Optional[str],
    flag: str,
    errors: FlagErrors,
) -> Optional[datetime]:
    if raw is None:
        errors.add(f"missing required {flag} (ISO-8601 UTC, e.g. 2024-05-01T08:00:00Z)")
        return None
    moment = parse_timestamp(raw)
    if moment is None:
        errors.add(f"invalid {flag} '{raw}'; must be an ISO-8601 timestamp")
        return None
    return moment


# --------------------------------------------------------------------------- #
# Commands: jobs
# --------------------------------------------------------------------------- #

def command_jobs_create(args: argparse.Namespace, store_path: Path) -> int:
    errors = FlagErrors()
    job_type = validate_job_type(args.type, errors)
    staves = validate_integer(args.staves, "--staves", 3, errors)
    width = validate_width(args.width, errors)
    errors.raise_if_any()

    store = load_store(store_path)
    job = {
        "id": uuid.uuid4().hex,
        "type": job_type,
        "staves": staves,
        "width": width,
        "created_at": format_timestamp(utc_now()),
    }
    store["jobs"].append(job)
    save_store(store_path, store)

    emit(accent(job["id"]))
    emit_hint(
        f"created {job['type']} job; next: staverift calc --staves {job['staves']} --width "
        f"{format_width(job['width'])}  |  staverift clamp start --job {job['id']} --band 1"
    )
    return EXIT_OK


def command_jobs_list(args: argparse.Namespace, store_path: Path) -> int:
    store = load_store(store_path)
    # Stable sort keeps insertion order when several jobs share a timestamp.
    jobs = sorted(store["jobs"], key=lambda job: str(job.get("created_at", "")))

    if args.json:
        emit(json.dumps(jobs, indent=2, sort_keys=True))
        return EXIT_OK

    if not jobs:
        emit("no jobs yet; create one with: staverift jobs create --type barrel --staves 30 --width 90")
        return EXIT_OK

    rows = [
        [
            str(job.get("id", "")),
            str(job.get("type", "")),
            str(job.get("staves", "")),
            format_width(job.get("width")),
            str(job.get("created_at", "")),
        ]
        for job in jobs
    ]
    emit_hint(f"{len(jobs)} job{'s' if len(jobs) != 1 else ''}, widths in mm")
    for line in render_table(
        ["ID", "TYPE", "STAVES", "WIDTH", "CREATED"],
        rows,
        ["left", "left", "right", "right", "left"],
    ):
        emit(line)
    return EXIT_OK


def command_jobs_show(args: argparse.Namespace, store_path: Path) -> int:
    store = load_store(store_path)
    job = find_job(store, args.id)
    total = total_clamped_minutes(store, str(job.get("id", "")))

    if args.json:
        payload = dict(job)
        payload["total_clamped_minutes"] = total
        emit(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK

    entries = job_clamps(store, str(job.get("id", "")))
    open_count = sum(1 for entry in entries if entry.get("ended_at") is None)
    emit(f"job {accent(str(job.get('id', '')))}")
    emit(f"  type:      {job.get('type', '')}")
    emit(f"  staves:    {job.get('staves', '')}")
    emit(f"  width:     {format_width(job.get('width'))} mm")
    emit(f"  created:   {job.get('created_at', '')}")
    clamp_note = f"{len(entries)}"
    if open_count:
        clamp_note += f" ({accent('open')} on band {_open_band(entries)})"
    emit(f"  clamps:    {clamp_note}")
    emit(f"total clamped minutes: {total}")
    return EXIT_OK


def _open_band(entries: Sequence[Dict[str, Any]]) -> str:
    for entry in entries:
        if entry.get("ended_at") is None:
            return str(entry.get("band", "?"))
    return "?"


# --------------------------------------------------------------------------- #
# Commands: calc
# --------------------------------------------------------------------------- #

def command_calc(args: argparse.Namespace, _store_path: Path) -> int:
    errors = FlagErrors()
    staves = validate_integer(args.staves, "--staves", 3, errors)
    width = validate_width(args.width, errors)
    errors.raise_if_any()

    diameter, circumference = stave_geometry(int(staves), float(width))
    rows = [
        ["staves", str(staves)],
        ["width", f"{format_width(width)} mm"],
        ["diameter", accent(f"{diameter:.2f} mm")],
        ["circumference", accent(f"{circumference:.2f} mm")],
    ]
    emit_hint("diameter = width / sin(pi / staves); circumference = pi * diameter")
    for line in render_table(["FIELD", "VALUE"], rows, ["left", "right"]):
        emit(line)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Commands: clamp
# --------------------------------------------------------------------------- #

def command_clamp_start(args: argparse.Namespace, store_path: Path) -> int:
    errors = FlagErrors()
    band = validate_integer(args.band, "--band", 1, errors)
    errors.raise_if_any()

    store = load_store(store_path)
    job = find_job(store, args.job)
    job_id = str(job.get("id", ""))

    for entry in job_clamps(store, job_id):
        if entry.get("ended_at") is None:
            raise CliError(
                f"job {args.job} already has an open clamp on band {entry.get('band', '?')}",
                code=EXIT_STORE,
            )

    started_at = format_timestamp(utc_now())
    store["clamps"].append(
        {
            "id": uuid.uuid4().hex,
            "job_id": job_id,
            "band": band,
            "started_at": started_at,
            "ended_at": None,
            "minutes": None,
        }
    )
    save_store(store_path, store)

    emit(f"started band {accent(str(band))} on job {job_id} at {started_at}")
    emit_hint(f"stop it with: staverift clamp stop --job {job_id}")
    return EXIT_OK


def command_clamp_stop(args: argparse.Namespace, store_path: Path) -> int:
    store = load_store(store_path)
    job = find_job(store, args.job)
    job_id = str(job.get("id", ""))

    open_entries = [entry for entry in job_clamps(store, job_id) if entry.get("ended_at") is None]
    if not open_entries:
        raise CliError(f"no open clamp for job {args.job}", code=EXIT_STORE)
    entry = max(open_entries, key=lambda item: str(item.get("started_at", "")))

    stopped_at = utc_now()
    started_at = parse_timestamp(str(entry.get("started_at", ""))) or stopped_at
    elapsed_seconds = max((stopped_at - started_at).total_seconds(), 0.0)
    minutes = round(elapsed_seconds / 60.0, 2)

    entry["ended_at"] = format_timestamp(stopped_at)
    entry["minutes"] = minutes
    save_store(store_path, store)

    emit(
        f"stopped band {accent(str(entry.get('band', '?')))} on job {job_id} at "
        f"{entry['ended_at']} ({format_minutes(minutes)} minutes)"
    )
    return EXIT_OK


def command_clamp_backfill(args: argparse.Namespace, store_path: Path) -> int:
    errors = FlagErrors()
    band = validate_integer(args.band, "--band", 1, errors)
    started = validate_timestamp(args.start, "--start", errors)
    ended = validate_timestamp(args.end, "--end", errors)
    if started is not None and ended is not None and ended <= started:
        errors.add(f"invalid --end '{args.end}'; must be after --start")
    errors.raise_if_any()

    store = load_store(store_path)
    job = find_job(store, args.job)
    job_id = str(job.get("id", ""))

    minutes = round((ended - started).total_seconds() / 60.0, 2)
    entry = {
        "id": uuid.uuid4().hex,
        "job_id": job_id,
        "band": band,
        "started_at": format_timestamp(started),
        "ended_at": format_timestamp(ended),
        "minutes": minutes,
    }
    store["clamps"].append(entry)
    save_store(store_path, store)

    emit(
        f"backfilled band {accent(str(band))} on job {job_id}: {minutes} minutes "
        f"({entry['started_at']} {arrow()} {entry['ended_at']})"
    )
    return EXIT_OK


def command_clamp_list(args: argparse.Namespace, store_path: Path) -> int:
    store = load_store(store_path)
    job_id: Optional[str] = None
    if args.job:
        job_id = str(find_job(store, args.job).get("id", ""))

    entries = [entry for entry in store["clamps"] if job_id is None or entry.get("job_id") == job_id]
    entries.sort(key=lambda item: (str(item.get("started_at", "")), str(item.get("band", ""))))

    if args.json:
        emit(json.dumps(entries, indent=2, sort_keys=True))
        return EXIT_OK

    if not entries:
        emit("no clamp entries yet; start one with: staverift clamp start --job <id> --band 1")
        return EXIT_OK

    rows = []
    for entry in entries:
        is_open = entry.get("ended_at") is None
        rows.append(
            [
                str(entry.get("band", "")),
                str(entry.get("job_id", ""))[:12],
                str(entry.get("started_at", "")),
                accent("open") if is_open else str(entry.get("ended_at", "")),
                "-" if is_open else format_minutes(entry.get("minutes")),
            ]
        )
    emit_hint(f"{len(entries)} clamp entr{'ies' if len(entries) != 1 else 'y'}")
    for line in render_table(
        ["BAND", "JOB", "START", "END", "MINUTES"],
        rows,
        ["right", "left", "left", "left", "right"],
    ):
        emit(line)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Argument parser
# --------------------------------------------------------------------------- #

def _add_store_flag(parser: argparse.ArgumentParser, top_level_default: Any) -> None:
    parser.add_argument(
        "--store",
        metavar="PATH",
        default=top_level_default,
        help=f"store file (default: ${STORE_ENV_VAR} or {DEFAULT_STORE})",
    )


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="print only the JSON document on stdout (empty result prints [])",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="staverift",
        description="Typed coopering jobs, stave diameter calculator and clamp time log.",
        epilog=(
            "Store: --store PATH, else $STAVERIFT_STORE, else ~/.staverift/store.json\n"
            "Validation errors exit 2; store and lookup failures exit 1."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"staverift {__version__}")
    _add_store_flag(parser, None)

    subcommands = parser.add_subparsers(dest="command", metavar="command")

    jobs = subcommands.add_parser(
        "jobs",
        help="create, list and show coopering jobs",
        description="Create, list and show coopering jobs.",
    )
    _add_store_flag(jobs, argparse.SUPPRESS)
    jobs.set_defaults(group_parser=jobs)
    jobs_sub = jobs.add_subparsers(dest="jobs_command", metavar="command")

    create = jobs_sub.add_parser(
        "create",
        help="create a job (prints the new job id)",
        description=(
            "Create a coopering job. Cask type must be one of: " + ALLOWED_TYPES + "."
        ),
    )
    _add_store_flag(create, argparse.SUPPRESS)
    create.add_argument("--type", metavar="T", help=f"cask type: {ALLOWED_TYPES}")
    create.add_argument("--staves", metavar="N", help="stave count, integer >= 3")
    create.add_argument("--width", metavar="MM", help="stave width in mm, number > 0")
    create.set_defaults(handler=command_jobs_create)

    jobs_list = jobs_sub.add_parser("list", help="list jobs", description="List jobs.")
    _add_store_flag(jobs_list, argparse.SUPPRESS)
    _add_json_flag(jobs_list)
    jobs_list.set_defaults(handler=command_jobs_list)

    show = jobs_sub.add_parser(
        "show",
        help="show one job with its total clamped minutes",
        description="Show one job with its computed total clamped minutes.",
    )
    _add_store_flag(show, argparse.SUPPRESS)
    _add_json_flag(show)
    show.add_argument("id", metavar="ID", help="job id (a unique prefix of 4+ characters works)")
    show.set_defaults(handler=command_jobs_show)

    calc = subcommands.add_parser(
        "calc",
        help="compute stave-set diameter and circumference",
        description="Compute diameter and circumference from a stave count and stave width.",
    )
    _add_store_flag(calc, argparse.SUPPRESS)
    calc.add_argument("--staves", metavar="N", help="stave count, integer >= 3")
    calc.add_argument("--width", metavar="MM", help="stave width in mm, number > 0")
    calc.set_defaults(handler=command_calc)

    clamp = subcommands.add_parser(
        "clamp",
        help="log clamp time per band",
        description="Log how long each band was clamped on a job.",
    )
    _add_store_flag(clamp, argparse.SUPPRESS)
    clamp.set_defaults(group_parser=clamp)
    clamp_sub = clamp.add_subparsers(dest="clamp_command", metavar="command")

    start = clamp_sub.add_parser(
        "start",
        help="open a clamp session",
        description="Open a clamp session; --band is required.",
    )
    _add_store_flag(start, argparse.SUPPRESS)
    start.add_argument("--job", metavar="ID", required=True, help="job id")
    start.add_argument("--band", metavar="N", help="band number, integer >= 1 (required)")
    start.set_defaults(handler=command_clamp_start)

    stop = clamp_sub.add_parser(
        "stop",
        help="close the most recent open session",
        description="Close the most recent open clamp session and store the minutes.",
    )
    _add_store_flag(stop, argparse.SUPPRESS)
    stop.add_argument("--job", metavar="ID", required=True, help="job id")
    stop.set_defaults(handler=command_clamp_stop)

    backfill = clamp_sub.add_parser(
        "backfill",
        help="insert an already-finished session",
        description="Insert a clamp session that already happened.",
    )
    _add_store_flag(backfill, argparse.SUPPRESS)
    backfill.add_argument("--job", metavar="ID", required=True, help="job id")
    backfill.add_argument("--band", metavar="N", help="band number, integer >= 1 (required)")
    backfill.add_argument("--start", metavar="ISO", help="start instant, e.g. 2024-05-01T08:00:00Z")
    backfill.add_argument("--end", metavar="ISO", help="end instant, e.g. 2024-05-01T10:30:00Z")
    backfill.set_defaults(handler=command_clamp_backfill)

    clamp_list = clamp_sub.add_parser(
        "list",
        help="list clamp entries",
        description="List clamp entries, optionally filtered to one job.",
    )
    _add_store_flag(clamp_list, argparse.SUPPRESS)
    _add_json_flag(clamp_list)
    clamp_list.add_argument("--job", metavar="ID", help="only entries for this job")
    clamp_list.set_defaults(handler=command_clamp_list)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exit_request:
        return int(exit_request.code) if isinstance(exit_request.code, int) else EXIT_STORE

    handler = getattr(args, "handler", None)
    if handler is None:
        group = getattr(args, "group_parser", None)
        (group or parser).print_help(sys.stdout)
        return EXIT_OK

    store_path = resolve_store_path(getattr(args, "store", None))
    try:
        return handler(args, store_path)
    except CliError as error:
        for message in error.messages:
            print(message, file=sys.stderr)
        return error.code
    except BrokenPipeError:  # piping into head is not a crash
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
