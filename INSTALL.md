# Installing StaveRift

StaveRift is a single Python file with no runtime dependencies. Installation
only puts the `staverift` command on your `PATH` and installs the test runner.

## Prerequisites (install these yourself, with your OS tools)

- **Python 3.9 or newer.** Check with `python3 --version`. Python 3.11 is what
  the project is developed and tested on.
- **pip** for that interpreter: `python3 -m pip --version`.
- **bash** — only needed for `demo.sh` / `demo/walkthrough.sh` and
  `install.sh`. The tool itself runs on any shell.

Nothing else. No database, no compiler, no system libraries, no service to
start.

## Package manager

One only: **pip** (with `requirements.txt` and `pyproject.toml` at the project
root). Do not mix in conda, poetry, pipenv or pyenv — the project assumes a
plain `python3` interpreter.

## Steps

1. **Install dependencies and the command** (exits when done; it never starts
   anything in the foreground):

   ```bash
   bash install.sh
   ```

   That runs, in order: `pip install --upgrade pip setuptools wheel`,
   `pip install -r requirements.txt` (the test runner), and
   `pip install --no-build-isolation -e .` (the `staverift` console script),
   then a cheap import self-check. Safe to re-run.

2. **Seed sample data (optional).** StaveRift needs no seed: a missing store is
   an empty log by design, and `jobs list` tells you the exact command to run
   first. If you want a populated log immediately, the walkthrough builds a
   scratch one (it never touches `~/.staverift/store.json`):

   ```bash
   bash demo.sh
   ```

   Read the path it prints at the end to browse the resulting JSON, or point it
   at your real log on purpose:

   ```bash
   STAVERIFT_DEMO_DIR="$HOME/.staverift" bash demo/walkthrough.sh
   ```

3. **Run it.**

   ```bash
   staverift --help
   ```

   or, without installing anything:

   ```bash
   python3 staverift.py --help
   ```

4. **Run the tests.**

   ```bash
   python3 -m pytest -q
   ```

   The suite also runs on the standard library alone:
   `python3 -m unittest discover -s tests`.

## Manual install, if you prefer no script

```bash
python3 -m pip install --upgrade pip setuptools wheel
python3 -m pip install --no-build-isolation -e .   # optional: console script
python3 staverift.py --version                     # works with no install at all
```

## Choosing a store location

Nothing has to be configured. Defaults, in precedence order:

1. `--store PATH`
2. `$STAVERIFT_STORE`
3. `~/.staverift/store.json`

`$STAVERIFT_STORE` is read from the environment; no `.env` file is loaded (see
`.env.example`). An empty value is treated as unset.

## Uninstall

```bash
python3 -m pip uninstall staverift
rm -rf ~/.staverift        # this deletes your job and clamp log — be sure
```
