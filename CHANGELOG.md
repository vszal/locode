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

### Changed
- `__version__` is now single-sourced from package metadata instead of a
  hardcoded literal.
