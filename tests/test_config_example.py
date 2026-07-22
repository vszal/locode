"""`config.toml.example` is the reference for every Config field.

AGENTS.md makes keeping it in sync a standing requirement, but a requirement
nobody checks goes stale the first time someone adds a field in a hurry. These
tests make the drift a test failure instead of a documentation bug.
"""

import tomllib
from dataclasses import fields
from pathlib import Path

import pytest

from locode.config import Config

EXAMPLE = Path(__file__).resolve().parent.parent / "config.toml.example"

# Sections that map 1:1 onto a dataclass. [permissions], [aliases] and
# [thinking] are free-form key spaces, not fixed field sets, so they're
# deliberately excluded.
SECTIONS = ["server", "model", "agent", "editor", "web", "ui"]


@pytest.fixture(scope="module")
def raw():
    return tomllib.loads(EXAMPLE.read_text())


@pytest.mark.parametrize("section", SECTIONS)
def test_every_field_is_documented(raw, section):
    declared = {f.name for f in fields(getattr(Config(), section))}
    documented = set(raw.get(section, {}))
    assert not declared - documented, (
        f"[{section}] in config.toml.example is missing "
        f"{sorted(declared - documented)}")


@pytest.mark.parametrize("section", SECTIONS)
def test_no_documented_key_is_unknown(raw, section):
    declared = {f.name for f in fields(getattr(Config(), section))}
    documented = set(raw.get(section, {}))
    assert not documented - declared, (
        f"[{section}] in config.toml.example documents fields that no longer "
        f"exist: {sorted(documented - declared)}")


@pytest.mark.parametrize("section", SECTIONS)
def test_documented_values_match_the_defaults(raw, section):
    """The example is annotated *with the defaults*, so a stale value there is
    just as misleading as a missing key."""
    obj = getattr(Config(), section)
    for f in fields(obj):
        if f.name not in raw.get(section, {}):
            continue
        assert raw[section][f.name] == getattr(obj, f.name), (
            f"[{section}].{f.name} in config.toml.example says "
            f"{raw[section][f.name]!r} but the default is "
            f"{getattr(obj, f.name)!r}")
