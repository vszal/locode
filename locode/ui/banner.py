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


def art(color: bool = True) -> str:
    """Just the block letters. Split from the status row so a caller can show
    the splash immediately and print the status only once it's true — printing
    both up front is what left a stale "server: down" on screen after the model
    had finished loading."""
    return f"{ACCENT}{BANNER}{RESET}" if color else BANNER


def status(model: str, server_up: bool, cwd: str, version: str,
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
    line = f"  {dot_model} {model}   {dot_server} {server_txt}   {cwd}   v{version}"
    hint = "  type a task, /help for commands, Esc to interrupt"
    if color:
        line = f"{DIM}{line}{RESET}"
        hint = f"{DIM}{hint}{RESET}"
    return f"{line}\n{hint}"


def render(model: str, server_up: bool, cwd: str, version: str,
           color: bool = True, model_up: bool | None = None) -> str:
    """Art + status in one string, for callers with nothing to wait on."""
    return (f"{art(color)}\n\n"
            f"{status(model, server_up, cwd, version, color, model_up)}")
