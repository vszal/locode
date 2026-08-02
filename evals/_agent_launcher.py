#!/usr/bin/env python3
"""Run `locode`'s CLI from a SPECIFIC source tree, not the installed one.

The paired A/B (`ab.py`) needs two versions of the agent alive in one session,
one of them checked out in a git worktree. The obvious routes don't work:

  - `PYTHONPATH=<worktree>` does nothing. The editable install registers a
    `sys.meta_path` finder (`__editable___locode_*_finder`) that maps the
    package name straight to the main repo, and meta-path finders are consulted
    BEFORE `sys.path`. Verified: with PYTHONPATH set to a decoy tree, `import
    locode` still resolved to the main checkout.
  - Building a venv per worktree costs a dependency install per arm, for two
    trees that differ only in the `locode/` package.

So: drop the editable finder, put the requested tree first on `sys.path`, and
import from there — while keeping the main venv's site-packages for `httpx` and
`prompt_toolkit`, which are the same in both arms by construction.

Usage:  python _agent_launcher.py <agent-root> [locode args...]

The import is then VERIFIED to have come from `<agent-root>` and the process
exits non-zero if it did not. That check is the point of this file: an A/B whose
two arms silently ran the same code produces a clean-looking zero delta, which
is indistinguishable from "the change had no effect" and would be believed.
"""
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: _agent_launcher.py <agent-root> [locode args...]",
              file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    if not (root / "locode" / "__init__.py").is_file():
        print(f"_agent_launcher: no locode package under {root}", file=sys.stderr)
        return 2

    # Remove the editable-install finder(s) so the name `locode` is resolved by
    # the normal sys.path machinery, which we control below. Matching on the
    # module name is how setuptools names these; if a future install method uses
    # something else, the verification below still catches it.
    sys.meta_path = [f for f in sys.meta_path
                     if not getattr(type(f), "__module__", "").startswith("__editable__")]
    sys.path.insert(0, str(root))

    import locode
    # The whole reason this shim exists. Compare resolved paths, not strings:
    # a worktree path can be a symlink (/tmp -> /private/tmp on macOS).
    got = Path(locode.__file__).resolve().parent.parent
    if got != root:
        print(f"_agent_launcher: refusing to run — asked for the agent at "
              f"{root} but `import locode` resolved to {got}. The A/B would "
              f"have compared a tree against itself.", file=sys.stderr)
        return 2

    from locode.cli import main as cli_main
    # Hand the CLI an argv that looks like a normal `locode` invocation, so
    # nothing downstream can tell it was launched through a shim.
    sys.argv = ["locode"] + sys.argv[2:]
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
