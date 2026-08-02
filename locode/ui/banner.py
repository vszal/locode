"""The locode splash banner (ANSI-shadow block letters, width-verified)."""

BANNER = r"""
██╗     ██████╗  ██████╗  ██████╗ ██████╗ ███████╗
██║     ██╔═══██╗██╔════╝██╔═══██╗██╔══██╗██╔════╝
██║     ██║   ██║██║     ██║   ██║██║  ██║█████╗
██║     ██║   ██║██║     ██║   ██║██║  ██║██╔══╝
███████╗╚██████╔╝╚██████╗╚██████╔╝██████╔╝███████╗
╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
        local-first agentic coding · mlx · :8081"""

# ANSI colors, used only when stdout is a TTY that supports them.
ACCENT = "\033[36m"   # cyan
DIM = "\033[2m"
RESET = "\033[0m"


def render(model: str, server_up: bool, cwd: str, version: str,
           color: bool = True, model_up: bool | None = None) -> str:
    """Status line: one dot for the selected MODEL, one for the SERVER.

    They are genuinely different facts — mlx serves one model at a time, so a
    server can be up while the model you asked for is not the one loaded.
    `model_up` defaults to `server_up` for callers that can't tell them apart
    (`--logo`, which talks to nothing).
    """
    if model_up is None:
        model_up = server_up
    dot_model = "●" if model_up else "○"
    dot_server = "●" if server_up else "○"
    server_txt = "server: up" if server_up else "server: down"
    banner = f"{ACCENT}{BANNER}{RESET}" if color else BANNER
    status = f"  {dot_model} {model}   {dot_server} {server_txt}   {cwd}   v{version}"
    hint = "  type a task, /help for commands, Esc to interrupt"
    if color:
        status = f"{DIM}{status}{RESET}"
        hint = f"{DIM}{hint}{RESET}"
    return f"{banner}\n\n{status}\n{hint}"
