"""locode entrypoint: argument parsing, headless (-p) vs. interactive REPL."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from locode import __version__
from locode.config import Config
from locode.model.client import ModelClient
from locode.permissions import AUTO, PermissionPolicy
from locode.scaffold import ensure_user_config, first_run_notice
from locode.server.manager import SingleGpuManager
from locode.tools import build_registry


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="locode",
                                description="Agentic CLI for local LLMs (mlx).")
    p.add_argument("prompt", nargs="*", help="Run one headless turn and exit.")
    p.add_argument("-p", "--print", action="store_true",
                   help="Headless: run a single turn from the prompt/stdin.")
    p.add_argument("-m", "--model", help="Model alias or full id to use.")
    p.add_argument("--host", help="Server host/IP (default 127.0.0.1).")
    p.add_argument("--port", type=int, help="Server port (default 8081).")
    p.add_argument("--base-url", dest="base_url",
                   help="Full server URL (e.g. https://gpu-box:8081); overrides "
                        "host/port and marks the endpoint remote.")
    p.add_argument("--allow-tool", default="",
                   help="Comma list of ASK tools to auto-allow (headless).")
    p.add_argument("--yolo", action="store_true",
                   help="Flip ASK tools to AUTO (deny_paths still enforced).")
    p.add_argument("--no-splash", action="store_true", help="Suppress the banner.")
    p.add_argument("--no-markdown", action="store_true",
                   help="Stream raw tokens instead of line-buffered markdown.")
    p.add_argument("--logo", action="store_true", help="Print the banner and exit.")
    p.add_argument("--version", action="version", version=f"locode {__version__}")
    return p


def _assemble(args):
    # First run: scaffold a starter config from the template, then load it so the
    # example aliases are immediately resolvable. Notice goes to stderr so it
    # never corrupts headless stdout.
    if ensure_user_config():
        print(first_run_notice(), file=sys.stderr)
    cfg = Config.load().override(model=args.model, port=args.port,
                                 host=args.host, base_url=args.base_url)
    if args.no_markdown:
        cfg.ui.markdown = False
    manager = SingleGpuManager(cfg)
    client = ModelClient(cfg.base_url)
    registry = build_registry(cfg)
    return cfg, manager, client, registry


async def _headless(args) -> int:
    cfg, manager, client, registry = _assemble(args)
    text = " ".join(args.prompt).strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        print("error: no prompt given", file=sys.stderr)
        return 2
    from locode.agent.loop import AgentLoop

    policy = PermissionPolicy(cfg.permissions, yolo=args.yolo)
    for t in (x.strip() for x in args.allow_tool.split(",") if x.strip()):
        policy.remember(t, AUTO)
    # Headless: no confirm/select -> ASK tools that weren't pre-allowed are denied.
    loop = AgentLoop(client, manager, registry, policy, cfg, cwd=str(Path.cwd()),
                     on_delta=lambda s: (sys.stdout.write(s), sys.stdout.flush()))
    try:
        result = await loop.run_turn(text)
    except Exception as e:
        print(f"\n[error] {e}", file=sys.stderr)
        return 1
    if result and result.startswith(("⛔", "⏹")):
        print(f"\n{result}")
    else:
        print()
    return 0


async def _interactive(args) -> int:
    cfg, manager, client, registry = _assemble(args)
    from locode.ui.repl import Repl

    repl = Repl(cfg, client, manager, registry, yolo=args.yolo)
    return await repl.run(splash=not args.no_splash)


def _cmd_upgrade(argv: list[str]) -> int:
    """`locode upgrade [--check] [--pre]`: update locode in place per its
    recorded install method (see install.py / upgrade.py)."""
    import subprocess

    from locode import install
    from locode.upgrade import upgrade_argv

    p = argparse.ArgumentParser(prog="locode upgrade",
                                description="Update locode in place.")
    p.add_argument("--check", action="store_true",
                   help="Show the install method and planned commands; run nothing.")
    p.add_argument("--pre", action="store_true",
                   help="Allow pre-release versions (PyPI install methods).")
    a = p.parse_args(argv)

    m = install.read_method()
    if m is None:
        print("locode: can't tell how locode was installed (no install-method "
              "marker). Upgrade with your package manager directly, or reinstall "
              "via install.sh.", file=sys.stderr)
        return 1
    cmds = upgrade_argv(m.method, m.detail, pre=a.pre)
    label = m.method + (f" ({m.detail})" if m.detail else "")
    if a.check:
        print(f"locode {__version__} — installed via {label}")
        print("would run:")
        for c in cmds:
            print("  " + " ".join(c))
        return 0
    print(f"Upgrading locode (installed via {label})…", file=sys.stderr)
    for c in cmds:
        print("$ " + " ".join(c), file=sys.stderr)
        rc = subprocess.run(c).returncode
        if rc != 0:
            print(f"locode: upgrade step failed (exit {rc}).", file=sys.stderr)
            return rc
    return 0


def _cmd_uninstall(argv: list[str]) -> int:
    """`locode uninstall [--purge] [--yes]`: remove locode per its recorded
    install method. The plan is built in uninstall.py; deletion happens here,
    behind a confirmation."""
    import shutil
    import subprocess

    from locode import install
    from locode.uninstall import uninstall_plan

    p = argparse.ArgumentParser(prog="locode uninstall",
                                description="Remove locode.")
    p.add_argument("--purge", action="store_true",
                   help="Also remove config, state, and data dirs.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Don't prompt for confirmation.")
    a = p.parse_args(argv)

    m = install.read_method()
    if m is None:
        print("locode: can't tell how locode was installed (no install-method "
              "marker). Remove it with your package manager directly.",
              file=sys.stderr)
        return 1
    commands, paths = uninstall_plan(m.method, m.detail, purge=a.purge)

    print("This will:")
    for c in commands:
        print("  run:    " + " ".join(c))
    for path in paths:
        print(f"  delete: {path}")
    if not a.yes:
        try:
            reply = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 1

    rc = 0
    for c in commands:
        if subprocess.run(c).returncode != 0:
            print(f"locode: step failed: {' '.join(c)}", file=sys.stderr)
            rc = 1
    for path in paths:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
        except OSError as e:
            print(f"locode: could not remove {path}: {e}", file=sys.stderr)
            rc = 1
    return rc


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] == "upgrade":
        return _cmd_upgrade(raw[1:])
    if raw and raw[0] == "uninstall":
        return _cmd_uninstall(raw[1:])
    args = build_parser().parse_args(raw)
    if args.logo:
        from locode.ui import banner, render
        print(banner.render(args.model or "qwen14", False, str(Path.cwd()),
                            __version__, color=render.should_color()))
        return 0
    headless = args.print or (args.prompt and not sys.stdin.isatty())
    try:
        if headless:
            return asyncio.run(_headless(args))
        return asyncio.run(_interactive(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
