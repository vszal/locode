"""__version__ is single-sourced from package metadata, not a hardcoded literal."""

from importlib.metadata import version

import locode


def test_version_is_nonempty_string():
    assert isinstance(locode.__version__, str)
    assert locode.__version__


def test_version_matches_installed_metadata():
    # locode is installed (editable) in the dev/test env, so the runtime
    # __version__ must equal what importlib.metadata reports for the package.
    assert locode.__version__ == version("locode")
