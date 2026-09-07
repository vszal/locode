"""Where the per-turn progress grant defaults, per mode.

Headless (-p) must default to a flat, non-extendable wallclock so that
`locode bench` and the eval harness — both of which drive locode through -p —
stay bounded and stay comparable with every archived sweep (rule 91).
Interactive leaves the config default alone. An explicit --progress-grant wins
in either direction. See ROADMAP 5.144.
"""

import locode.cli as cli


def _args(argv):
    return cli.build_parser().parse_args(argv)


def test_headless_defaults_to_a_flat_wallclock():
    assert cli._progress_grant(_args(["-p", "do it"])) == 0.0


def test_interactive_leaves_the_config_default_alone(monkeypatch):
    # None means "don't override" — the value comes from AgentConfig/config.toml.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    assert cli._progress_grant(_args([])) is None


def test_a_piped_prompt_counts_as_headless(monkeypatch):
    # `echo do it | locode` is a one-shot run even without -p, and a benchmark
    # harness may well invoke it that way.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli._progress_grant(_args(["do", "it"])) == 0.0


def test_an_explicit_flag_overrides_the_headless_default():
    args = _args(["-p", "do it", "--progress-grant", "120"])
    assert cli._progress_grant(args) == 120.0


def test_an_explicit_zero_survives_as_zero(monkeypatch):
    # 0 is meaningful (it turns the extension off), so it must not be swallowed
    # as "unset" by a truthiness check anywhere along the path.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    args = _args(["--progress-grant", "0"])
    assert cli._progress_grant(args) == 0.0


def test_the_override_actually_reaches_the_agent_config():
    from locode.config import Config
    cfg = Config().override(progress_grant=0.0)
    assert cfg.agent.progress_grant_seconds == 0.0
    assert Config().override(progress_grant=None).agent.progress_grant_seconds \
        == Config().agent.progress_grant_seconds
