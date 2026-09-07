"""The README's example configuration must stay loadable.

A published example that has drifted from the real Config dataclasses is worse
than no example: it fails for a new user on their first run, in the one place
they have no context to debug it. Parse the block out of the README and put it
through locode's own loader.
"""
import re
import tomllib
from pathlib import Path

import pytest

from locode.config import Config

README = Path(__file__).resolve().parent.parent / "README.md"


@pytest.fixture(scope="module")
def example_block() -> str:
    md = README.read_text(encoding="utf-8")
    m = re.search(r"### Example configuration.*?```toml\n(.*?)```", md, re.S)
    assert m, "README lost its '### Example configuration' toml block"
    return m.group(1)


def test_example_is_valid_toml(example_block):
    tomllib.loads(example_block)


def test_locode_loads_the_example(tmp_path, example_block):
    p = tmp_path / "config.toml"
    p.write_text(example_block, encoding="utf-8")
    cfg = Config.load(p)
    # every value the example states explicitly must survive the round trip
    assert cfg.model.default == "qwen38"
    assert cfg.model.default in cfg.aliases, "the default alias must be defined"
    assert cfg.agent.max_iterations == 150
    assert cfg.server.port == 8081
    assert cfg.server.memory_reserve_gb == 5.0
    assert cfg.permissions.deny_paths == ["~/.ssh", "~/.aws", "~/.config/gh"]
    assert cfg.thinking == {"qwythos9": "off"}


def test_example_default_matches_the_shipped_default(example_block):
    """The README shouldn't recommend a different default than locode ships."""
    p = tomllib.loads(example_block)
    assert p["model"]["default"] == Config().model.default


def test_readme_has_no_construction_notice():
    """Removed for the production release; don't let it creep back."""
    md = README.read_text(encoding="utf-8")
    assert "Work in progress" not in md
    assert "🚧" not in md


def test_readme_references_the_logo_that_exists():
    md = README.read_text(encoding="utf-8")
    m = re.search(r'<img src="([^"]+)"', md)
    assert m, "README lost its logo <img>"
    assert (README.parent / m.group(1)).is_file(), f"missing asset: {m.group(1)}"
