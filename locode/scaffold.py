"""First-run config scaffolding.

locode carries no personal model table in code; instead, the first time it runs
without a `config.toml` it writes a starter one the user edits to taste, and
prints a one-line notice pointing them at it. The aliases in the template are
*examples* (real 4-bit MLX models that run on Apple Silicon) — a starting point,
not a requirement: a full "org/model" id always works without an alias.
"""

from __future__ import annotations

from pathlib import Path

from locode.config import CONFIG_PATH

STARTER_CONFIG = """\
# locode configuration — edit to taste.
#
# Short aliases map to full Hugging Face model ids. The ones below are EXAMPLES;
# change them to whatever you've actually pulled. You can also skip aliases
# entirely and pass a full "org/model" id (with -m or /model).

[model]
default = "qwen14"          # which alias to load at startup

[aliases]
# alias       = "huggingface-org/model-id"
gemma12     = "rajaschitnis/gemma-4-12b-it-text-only-4bit-mlx"
qwen14      = "mlx-community/Qwen3-14B-4bit"
qwencoder14 = "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"
qwencoder30 = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
devstral24  = "lmstudio-community/Devstral-Small-2507-MLX-4bit"
qwen4i      = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
qwen06      = "mlx-community/Qwen3-0.6B-4bit"
phi4        = "mlx-community/phi-4-4bit"
gemma27     = "mlx-community/gemma-3-text-27b-it-4bit"

# --- optional: talk to a server elsewhere (not started/stopped by locode) ----
# [server]
# base_url = "https://gpu-box:8081"
# manage   = "no"

# --- optional: web search ----------------------------------------------------
# [web]
# search_provider = "duckduckgo"   # keyless default; or "tavily" / "brave"
# tavily_api_key  = "..."          # (env TAVILY_API_KEY / BRAVE_API_KEY also work)
"""


def ensure_user_config(path: Path | None = None) -> bool:
    """Write the starter config if none exists. Returns True iff it was created."""
    target = path or CONFIG_PATH
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(STARTER_CONFIG)
    return True


def first_run_notice(path: Path | None = None) -> str:
    target = path or CONFIG_PATH
    return (
        f"Welcome to locode! I created a starter config at {target}\n"
        f"It has example model aliases — edit it to match the models you've pulled "
        f"(or pass a full org/model id with -m). Adjust the [model] default there too."
    )
