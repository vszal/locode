import pytest

from locode.server import aliases


def test_no_personal_aliases_shipped():
    # Models live in the user's config.toml now, not in shipped code.
    assert aliases.ALIASES == {}
    assert aliases.known_aliases() == []


def test_full_id_passthrough():
    full = "org/Some-Model-4bit"
    assert aliases.resolve(full) == full
    # A slash means "already a full id".
    assert aliases.resolve("mlx-community/Qwen3-14B-4bit") == "mlx-community/Qwen3-14B-4bit"


def test_bare_alias_raises_pointing_to_config():
    with pytest.raises(KeyError) as exc:
        aliases.resolve("qwen14")
    msg = str(exc.value)
    assert "qwen14" in msg
    assert "config.toml" in msg  # tells the user where aliases come from


def test_builtin_table_remains_extensible(monkeypatch):
    # The resolver still honors the built-in table if it's ever populated.
    monkeypatch.setitem(aliases.ALIASES, "tmp", "org/Tmp-4bit")
    assert aliases.resolve("tmp") == "org/Tmp-4bit"
