# Changelog

All notable changes to locode are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/). `pyproject.toml` is the version
source of truth (`locode --version`).

## [Unreleased]

### Added
- **`install.sh`** — curl-able installer that drops locode into an isolated
  environment (`pipx` → `uv` → dedicated venv + `~/.local/bin` shim) and records
  the install method. Supports `--dev` (editable from source), `--pre`, and
  `--dry-run`.
- **`locode upgrade`** — updates locode in place per its recorded install
  method; `--check` previews without running, `--pre` allows pre-releases.
- **`locode uninstall`** — removes locode behind a confirmation prompt; `--purge`
  also drops the config, state, and data dirs.
- **Session persistence** — `/save [name]` writes the current conversation to
  `~/.local/state/locode/sessions/`, `/resume [name]` restores it (no name lists
  saved sessions). Names are sanitized so they can't escape the sessions dir.
- **`[thinking]` config override** — control a model's reasoning per model from
  `config.toml`, keyed by alias or model-id substring: `"on"` forces
  `enable_thinking=true`, `"off"` forces it false, `"auto"` omits the kwarg
  (template default). Layers over the built-in capability profile, so you can
  suppress a reasoning model we haven't profiled — or re-enable thinking on one
  we suppress — without editing source. (Generalizes the Qwythos-9B fix below.)

### Changed
- `__version__` is now single-sourced from package metadata instead of a
  hardcoded literal.

### Fixed
- **Qwythos-9B "hangs."** Qwythos is a reasoning model that emits chain-of-thought
  in a `reasoning` field before any `content`; since locode streams only `content`,
  long thinking showed nothing (looked hung) and could exhaust the token budget,
  failing the turn. Its profile now sets `thinking_arg=True` so the server launches
  with `enable_thinking=false` — agentic turns dropped from seconds-of-thinking (and
  intermittent multi-minute hangs) to a steady ~13s, 100% pass on the reliability
  probe. Added `scripts/model_reliability_probe.sh` to catch this class of
  regression for any model.
- **Silent wrong-model serving.** mlx's `/v1/models` lists the whole HF cache,
  not the resident model, so requesting a *cached* model that wasn't loaded
  (`-m <other>`, or `/model`) skipped the switch and silently served whatever
  was already in memory. The manager now reads the resident model from the
  server process's `--model` argument, so a different requested model actually
  triggers a reload (and `/server` reports the real loaded model).
