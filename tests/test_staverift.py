"""Tests for StaveRift: store round-trip, typed jobs, geometry and clamp logs.

Run with ``pytest`` or ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import staverift  # noqa: E402  (path setup has to happen first)

ENTRYPOINT = REPO_ROOT / "staverift.py"


def run_cli(args, store=None, env=None):
    """Invoke the CLI in-process: returns (exit code, stdout, stderr)."""
    argv = []
    if store is not None:
        argv += ["--store", str(store)]
    argv += [str(item) for item in args]

    stdout, stderr = io.StringIO(), io.StringIO()
    patched = {}
    if env:
        patched = {key: value for key, value in env.items()}
    with mock.patch.dict(os.environ, patched, clear=False):
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = staverift.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def run_process(args, env=None):
    """Invoke the CLI as a real subprocess so exit codes and tracebacks are real."""
    environment = dict(os.environ)
    environment.pop("STAVERIFT_STORE", None)
    if env:
        environment.update(env)
    completed = subprocess.run(
        [sys.executable, str(ENTRYPOINT)] + [str(item) for item in args],
        capture_output=True,
        text=True,
        env=environment,
    )
    return completed.returncode, completed.stdout, completed.stderr


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.root = Path(self._tempdir.name)
        self.store = self.root / "staverift" / "store.json"

    # -- helpers ----------------------------------------------------------- #

    def create_job(self, job_type="barrel", staves="30", width="90"):
        code, out, err = run_cli(
            ["jobs", "create", "--type", job_type, "--staves", staves, "--width", width],
            store=self.store,
        )
        self.assertEqual(code, 0, err)
        return out.strip()

    def read_store(self):
        return json.loads(self.store.read_text(encoding="utf-8"))

    def write_store(self, document):
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")


class HelpAndUsageTests(StoreTestCase):
    def test_help_lists_every_command(self):
        code, out, _ = run_process(["--help"])
        self.assertEqual(code, 0)
        for command in ("jobs", "calc", "clamp"):
            self.assertIn(command, out)

    def test_bare_invocation_prints_usage_and_exits_zero(self):
        code, out, _ = run_process([])
        self.assertEqual(code, 0)
        self.assertIn("usage: staverift", out)

    def test_unknown_command_exits_two_without_traceback(self):
        code, out, err = run_process(["bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage: staverift", err)
        self.assertNotIn("Traceback", err)

    def test_subcommand_help_is_complete(self):
        for command in (["jobs", "create"], ["jobs", "list"], ["jobs", "show"], ["calc"],
                        ["clamp", "start"], ["clamp", "stop"], ["clamp", "backfill"],
                        ["clamp", "list"]):
            code, out, err = run_process(command + ["--help"])
            self.assertEqual(code, 0, f"{command}: {err}")
            self.assertIn("usage: staverift", out)


class JobsCreateTests(StoreTestCase):
    def test_create_writes_typed_job_and_prints_id(self):
        job_id = self.create_job(job_type="barrel", staves="30", width="90")
        self.assertEqual(len(job_id), 32)
        document = self.read_store()
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(len(document["jobs"]), 1)
        job = document["jobs"][0]
        self.assertEqual(job["id"], job_id)
        self.assertEqual(job["type"], "barrel")
        self.assertEqual(job["staves"], 30)
        self.assertAlmostEqual(job["width"], 90.0)
        self.assertTrue(job["created_at"].endswith("Z"))
        self.assertEqual(document["clamps"], [])

    def test_store_is_indented_and_key_sorted(self):
        self.create_job()
        raw = self.store.read_text(encoding="utf-8")
        self.assertIn('\n  "clamps": [', raw)
        self.assertIn('\n  "schema_version": 1', raw)
        job_block = raw.split('"jobs": [', 1)[1]
        order = [job_block.index(key) for key in ('"created_at"', '"id"', '"staves"', '"type"', '"width"')]
        self.assertEqual(order, sorted(order))

    def test_every_type_in_the_vocabulary_is_accepted(self):
        for job_type in staverift.JOB_TYPES:
            job_id = self.create_job(job_type=job_type)
            self.assertEqual(len(job_id), 32)
        self.assertEqual(len(self.read_store()["jobs"]), len(staverift.JOB_TYPES))

    def test_unknown_type_is_rejected_and_store_untouched(self):
        code, out, err = run_cli(
            ["jobs", "create", "--type", "foo", "--staves", "30", "--width", "90"], store=self.store
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("invalid --type 'foo'; allowed: barrel, cask, firkin, tun", err)
        self.assertFalse(self.store.exists())

    def test_each_bad_flag_reports_its_own_message(self):
        code, out, err = run_cli(
            ["jobs", "create", "--type", "cask", "--staves", "2", "--width", "0"], store=self.store
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("invalid --staves '2'; must be an integer >= 3", err)
        self.assertIn("invalid --width '0'; must be a number > 0", err)
        self.assertNotIn("Traceback", err)

    def test_missing_flags_are_reported(self):
        code, _, err = run_cli(["jobs", "create"], store=self.store)
        self.assertEqual(code, 2)
        self.assertIn("missing required --type", err)
        self.assertIn("missing required --staves", err)
        self.assertIn("missing required --width", err)

    def test_non_numeric_width_is_rejected(self):
        code, _, err = run_cli(
            ["jobs", "create", "--type", "tun", "--staves", "20", "--width", "abc"], store=self.store
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid --width 'abc'; must be a number > 0", err)


class StoreTests(StoreTestCase):
    def test_corrupt_store_reports_path_and_recovery_hint(self):
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text("{not json", encoding="utf-8")
        for command in (["jobs", "list"], ["clamp", "list"], ["jobs", "create", "--type", "barrel",
                        "--staves", "30", "--width", "90"]):
            code, out, err = run_cli(command, store=self.store)
            self.assertEqual(code, 1, f"{command} -> {code}")
            self.assertEqual(out, "")
            self.assertIn(f"Cannot read store at {self.store}:", err)
            self.assertIn("Fix or delete the file to start fresh.", err)
            self.assertNotIn("Traceback", err)

    def test_corrupt_store_in_a_real_process_has_no_traceback(self):
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text("{not json", encoding="utf-8")
        code, out, err = run_process(["--store", self.store, "jobs", "list"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        self.assertIn("Fix or delete the file to start fresh.", err)

    def test_missing_store_is_an_empty_store(self):
        code, out, _ = run_cli(["jobs", "list"], store=self.store)
        self.assertEqual(code, 0)
        self.assertIn("no jobs yet", out)
        self.assertFalse(self.store.exists())

    def test_hand_edited_store_is_readable(self):
        self.write_store(
            {
                "schema_version": 1,
                "jobs": [
                    {"id": "a" * 32, "type": "firkin", "staves": 18, "width": 60, "created_at": "2024-04-01T09:00:00Z"}
                ],
                "clamps": [],
            }
        )
        code, out, _ = run_cli(["jobs", "list"], store=self.store)
        self.assertEqual(code, 0)
        self.assertIn("firkin", out)

    def test_store_flag_beats_environment_beats_default(self):
        flag_store = self.root / "flag.json"
        env_store = self.root / "env.json"
        fake_home = self.root / "home"
        fake_home.mkdir()

        with mock.patch.dict(os.environ, {"HOME": str(fake_home), "STAVERIFT_STORE": str(env_store)}):
            self.assertEqual(run_cli(["jobs", "list"], store=flag_store)[0], 0)
            self.assertEqual(run_cli(["jobs", "create", "--type", "barrel", "--staves", "30",
                                      "--width", "90"], store=flag_store)[0], 0)
            self.assertTrue(flag_store.exists())
            self.assertFalse(env_store.exists())

            self.assertEqual(run_cli(["jobs", "create", "--type", "tun", "--staves", "20",
                                      "--width", "70"])[0], 0)
            self.assertTrue(env_store.exists())

        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            os.environ.pop("STAVERIFT_STORE", None)
            self.assertEqual(run_cli(["jobs", "create", "--type", "cask", "--staves", "24",
                                      "--width", "75"])[0], 0)
        self.assertTrue((fake_home / ".staverift" / "store.json").exists())

    def test_empty_environment_value_falls_back_to_default(self):
        fake_home = self.root / "home"
        fake_home.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(fake_home), "STAVERIFT_STORE": ""}):
            code, _, _ = run_cli(["jobs", "create", "--type", "tun", "--staves", "20", "--width", "70"])
        self.assertEqual(code, 0)
        self.assertTrue((fake_home / ".staverift" / "store.json").exists())

    def test_writes_leave_no_temporary_files(self):
        self.create_job()
        self.create_job(job_type="cask")
        leftovers = [entry.name for entry in self.store.parent.iterdir() if entry.name != "store.json"]
        self.assertEqual(leftovers, [])


class CalcTests(StoreTestCase):
    def test_calc_echoes_inputs_and_computes_geometry(self):
        code, out, _ = run_cli(["calc", "--staves", "30", "--width", "90"])
        self.assertEqual(code, 0)
        self.assertIn("30", out)
        self.assertIn("90.00", out)
        diameter = round(90 / __import__("math").sin(__import__("math").pi / 30), 2)
        circumference = round(__import__("math").pi * (90 / __import__("math").sin(__import__("math").pi / 30)), 2)
        self.assertIn(f"{diameter:.2f}", out)
        self.assertIn(f"{circumference:.2f}", out)
        self.assertIn("diameter", out)
        self.assertIn("circumference", out)

    def test_calc_never_reads_the_store(self):
        broken = self.root / "broken.json"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("{not json", encoding="utf-8")
        code, out, err = run_cli(["calc", "--staves", "30", "--width", "90"], store=broken)
        self.assertEqual(code, 0, err)
        self.assertIn("diameter", out)

    def test_calc_rejects_too_few_staves(self):
        code, out, err = run_cli(["calc", "--staves", "2", "--width", "90"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("invalid --staves '2'; must be an integer >= 3", err)

    def test_calc_rejects_bad_width(self):
        code, _, err = run_cli(["calc", "--staves", "30", "--width", "abc"])
        self.assertEqual(code, 2)
        self.assertIn("invalid --width 'abc'; must be a number > 0", err)

    def test_geometry_matches_closed_form(self):
        diameter, circumference = staverift.stave_geometry(30, 90.0)
        self.assertAlmostEqual(diameter, 861.01, places=2)
        self.assertAlmostEqual(circumference, 2704.94, places=2)


class ClampStartStopTests(StoreTestCase):
    def test_start_prints_band_and_timestamp(self):
        job_id = self.create_job()
        code, out, err = run_cli(["clamp", "start", "--job", job_id, "--band", "1"], store=self.store)
        self.assertEqual(code, 0, err)
        self.assertIn("band 1", out)
        self.assertRegex(out, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
        entry = self.read_store()["clamps"][0]
        self.assertEqual(entry["job_id"], job_id)
        self.assertEqual(entry["band"], 1)
        self.assertTrue(entry["started_at"].endswith("Z"))
        self.assertIsNone(entry["ended_at"])
        self.assertIsNone(entry["minutes"])

    def test_missing_band_is_rejected(self):
        job_id = self.create_job()
        code, out, err = run_cli(["clamp", "start", "--job", job_id], store=self.store)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("missing required --band (integer >= 1)", err)

    def test_band_zero_is_rejected(self):
        job_id = self.create_job()
        code, _, err = run_cli(["clamp", "start", "--job", job_id, "--band", "0"], store=self.store)
        self.assertEqual(code, 2)
        self.assertIn("invalid --band '0'; must be an integer >= 1", err)

    def test_second_open_session_is_refused(self):
        job_id = self.create_job()
        run_cli(["clamp", "start", "--job", job_id, "--band", "1"], store=self.store)
        code, _, err = run_cli(["clamp", "start", "--job", job_id, "--band", "2"], store=self.store)
        self.assertEqual(code, 1)
        self.assertIn(f"job {job_id} already has an open clamp on band 1", err)
        self.assertEqual(len(self.read_store()["clamps"]), 1)

    def test_unknown_job_is_reported(self):
        code, _, err = run_cli(["clamp", "start", "--job", "nope", "--band", "1"], store=self.store)
        self.assertEqual(code, 1)
        self.assertIn("no job with id 'nope'", err)

    def test_stop_stores_computed_minutes(self):
        job_id = self.create_job()
        started = datetime.now(timezone.utc) - timedelta(minutes=90)
        self.write_store(
            {
                "schema_version": 1,
                "jobs": self.read_store()["jobs"],
                "clamps": [
                    {
                        "id": "b" * 32,
                        "job_id": job_id,
                        "band": 1,
                        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "ended_at": None,
                        "minutes": None,
                    }
                ],
            }
        )
        code, out, err = run_cli(["clamp", "stop", "--job", job_id], store=self.store)
        self.assertEqual(code, 0, err)
        self.assertIn("band 1", out)
        entry = self.read_store()["clamps"][0]
        self.assertIsNotNone(entry["ended_at"])
        self.assertTrue(entry["ended_at"].endswith("Z"))
        self.assertAlmostEqual(entry["minutes"], 90.0, delta=0.05)

    def test_stop_without_open_session_is_reported(self):
        job_id = self.create_job()
        code, _, err = run_cli(["clamp", "stop", "--job", job_id], store=self.store)
        self.assertEqual(code, 1)
        self.assertIn(f"no open clamp for job {job_id}", err)


class ClampBackfillTests(StoreTestCase):
    def test_backfill_reports_and_stores_minutes(self):
        job_id = self.create_job()
        code, out, err = run_cli(
            ["clamp", "backfill", "--job", job_id, "--band", "2",
             "--start", "2024-05-01T08:00:00Z", "--end", "2024-05-01T10:30:00Z"],
            store=self.store,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("150.0 minutes", out)
        entry = self.read_store()["clamps"][0]
        self.assertEqual(entry["band"], 2)
        self.assertEqual(entry["started_at"], "2024-05-01T08:00:00Z")
        self.assertEqual(entry["ended_at"], "2024-05-01T10:30:00Z")
        self.assertAlmostEqual(entry["minutes"], 150.0)

    def test_end_before_start_is_rejected(self):
        job_id = self.create_job()
        code, _, err = run_cli(
            ["clamp", "backfill", "--job", job_id, "--band", "2",
             "--start", "2024-05-01T10:00:00Z", "--end", "2024-05-01T09:00:00Z"],
            store=self.store,
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid --end '2024-05-01T09:00:00Z'; must be after --start", err)
        self.assertEqual(self.read_store()["clamps"], [])

    def test_missing_band_is_rejected(self):
        job_id = self.create_job()
        code, _, err = run_cli(
            ["clamp", "backfill", "--job", job_id,
             "--start", "2024-05-01T08:00:00Z", "--end", "2024-05-01T09:00:00Z"],
            store=self.store,
        )
        self.assertEqual(code, 2)
        self.assertIn("missing required --band (integer >= 1)", err)

    def test_unparsable_timestamp_is_rejected(self):
        job_id = self.create_job()
        code, _, err = run_cli(
            ["clamp", "backfill", "--job", job_id, "--band", "1",
             "--start", "yesterday", "--end", "2024-05-01T09:00:00Z"],
            store=self.store,
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid --start 'yesterday'; must be an ISO-8601 timestamp", err)


class JobsListShowTests(StoreTestCase):
    def test_list_prints_one_line_per_job(self):
        first = self.create_job(job_type="barrel")
        second = self.create_job(job_type="firkin", staves="18", width="60")
        code, out, _ = run_cli(["jobs", "list"], store=self.store)
        self.assertEqual(code, 0)
        self.assertIn(first, out)
        self.assertIn(second, out)
        self.assertIn("barrel", out)
        self.assertIn("firkin", out)
        self.assertIn("18", out)
        self.assertIn("60.00", out)

    def test_list_json_is_an_array_of_jobs(self):
        job_id = self.create_job()
        code, out, _ = run_cli(["jobs", "list", "--json"], store=self.store)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIsInstance(payload, list)
        self.assertEqual(payload[0]["id"], job_id)

    def test_empty_json_listing_is_brackets(self):
        code, out, _ = run_cli(["jobs", "list", "--json"], store=self.store)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "[]")

    def test_empty_human_listing_points_at_create(self):
        code, out, _ = run_cli(["jobs", "list"], store=self.store)
        self.assertEqual(code, 0)
        self.assertIn(
            "no jobs yet; create one with: staverift jobs create --type barrel --staves 30 --width 90", out
        )

    def test_show_totals_closed_clamps(self):
        job_id = self.create_job()
        for band, start, end in (
            ("1", "2024-05-01T08:00:00Z", "2024-05-01T08:30:00Z"),
            ("2", "2024-05-02T08:00:00Z", "2024-05-02T08:15:00Z"),
        ):
            run_cli(["clamp", "backfill", "--job", job_id, "--band", band, "--start", start, "--end", end],
                    store=self.store)
        code, out, _ = run_cli(["jobs", "show", job_id], store=self.store)
        self.assertEqual(code, 0)
        self.assertIn("barrel", out)
        self.assertIn("30", out)
        self.assertIn("90.00", out)
        self.assertIn("created", out)
        self.assertIn("total clamped minutes: 45.0", out)

    def test_show_json_adds_the_total(self):
        job_id = self.create_job()
        run_cli(["clamp", "backfill", "--job", job_id, "--band", "1",
                 "--start", "2024-05-01T08:00:00Z", "--end", "2024-05-01T08:45:00Z"], store=self.store)
        code, out, _ = run_cli(["jobs", "show", job_id, "--json"], store=self.store)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], job_id)
        self.assertEqual(payload["type"], "barrel")
        self.assertAlmostEqual(payload["total_clamped_minutes"], 45.0)

    def test_show_json_without_clamps_is_zero(self):
        job_id = self.create_job()
        code, out, _ = run_cli(["jobs", "show", job_id, "--json"], store=self.store)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["total_clamped_minutes"], 0)

    def test_show_unknown_job_is_an_error(self):
        self.create_job()
        code, _, err = run_cli(["jobs", "show", "nope"], store=self.store)
        self.assertEqual(code, 1)
        self.assertIn("no job with id 'nope'", err)

    def test_show_accepts_a_unique_id_prefix(self):
        job_id = self.create_job()
        code, out, _ = run_cli(["jobs", "show", job_id[:8], "--json"], store=self.store)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["id"], job_id)


class ClampListTests(StoreTestCase):
    def test_list_shows_stopped_and_backfilled_entries(self):
        job_id = self.create_job()
        run_cli(["clamp", "start", "--job", job_id, "--band", "1"], store=self.store)
        run_cli(["clamp", "stop", "--job", job_id], store=self.store)
        run_cli(["clamp", "backfill", "--job", job_id, "--band", "2",
                 "--start", "2024-05-01T08:00:00Z", "--end", "2024-05-01T10:30:00Z"], store=self.store)

        code, out, _ = run_cli(["clamp", "list", "--job", job_id], store=self.store)
        self.assertEqual(code, 0)
        data_lines = [line for line in out.splitlines() if line.strip() and "BAND" not in line and "─" not in line]
        self.assertEqual(len(data_lines), 2, out)
        self.assertIn("150.0", out)
        self.assertIn("2024-05-01T08:00:00Z", out)
        self.assertIn("2024-05-01T10:30:00Z", out)

    def test_list_filters_by_job(self):
        first = self.create_job(job_type="barrel")
        second = self.create_job(job_type="cask", staves="24", width="75")
        run_cli(["clamp", "backfill", "--job", first, "--band", "1",
                 "--start", "2024-05-01T08:00:00Z", "--end", "2024-05-01T08:10:00Z"], store=self.store)
        run_cli(["clamp", "backfill", "--job", second, "--band", "5",
                 "--start", "2024-05-02T08:00:00Z", "--end", "2024-05-02T08:20:00Z"], store=self.store)

        code, out, _ = run_cli(["clamp", "list", "--job", second], store=self.store)
        self.assertEqual(code, 0)
        self.assertIn("20.0", out)
        self.assertNotIn("10.0", out)

        code, payload, _ = run_cli(["clamp", "list", "--job", second, "--json"], store=self.store)
        self.assertEqual(code, 0)
        entries = json.loads(payload)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["job_id"], second)

    def test_empty_json_listing_is_brackets(self):
        code, out, _ = run_cli(["clamp", "list", "--json"], store=self.store)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "[]")

    def test_empty_human_listing_points_at_start(self):
        code, out, _ = run_cli(["clamp", "list"], store=self.store)
        self.assertEqual(code, 0)
        self.assertIn("no clamp entries yet; start one with: staverift clamp start --job <id> --band 1", out)

    def test_open_entry_is_shown_as_open(self):
        job_id = self.create_job()
        run_cli(["clamp", "start", "--job", job_id, "--band", "3"], store=self.store)
        code, out, _ = run_cli(["clamp", "list"], store=self.store)
        self.assertEqual(code, 0)
        self.assertIn("open", out)
        self.assertIn("3", out)


class WalkthroughScriptTests(StoreTestCase):
    def test_walkthrough_exits_zero_without_tracebacks(self):
        completed = subprocess.run(
            ["bash", str(REPO_ROOT / "demo" / "walkthrough.sh")],
            capture_output=True,
            text=True,
            env={**os.environ, "STAVERIFT_DEMO_DIR": str(self.root / "demo")},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("Traceback", completed.stdout + completed.stderr)
        self.assertIn("total clamped minutes", completed.stdout)


if __name__ == "__main__":
    unittest.main()
