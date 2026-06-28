import tomllib

from locode import scaffold


def test_creates_starter_config_when_missing(tmp_path):
    target = tmp_path / "config.toml"
    assert scaffold.ensure_user_config(target) is True
    assert target.exists()
    # Calling again is a no-op (does not clobber the user's edits).
    assert scaffold.ensure_user_config(target) is False


def test_starter_config_is_valid_toml_with_aliases(tmp_path):
    target = tmp_path / "config.toml"
    scaffold.ensure_user_config(target)
    data = tomllib.loads(target.read_text())
    assert "aliases" in data and data["aliases"]          # has example aliases
    assert "/" in next(iter(data["aliases"].values()))    # values are full HF ids
    assert data["aliases"][data["model"]["default"]]      # default resolves to an alias


def test_does_not_overwrite_existing(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('[model]\ndefault = "mine"\n')
    assert scaffold.ensure_user_config(target) is False
    assert "mine" in target.read_text()


def test_first_run_notice_mentions_path(tmp_path):
    target = tmp_path / "config.toml"
    assert str(target) in scaffold.first_run_notice(target)
