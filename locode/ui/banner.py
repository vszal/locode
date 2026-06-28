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
           color: bool = True) -> str:
    dot_model = "●" if server_up else "○"
    dot_server = "server: up" if server_up else "server: starting…"
    banner = f"{ACCENT}{BANNER}{RESET}" if color else BANNER
    status = f"  {dot_model} {model}   ○ {dot_server}   {cwd}   v{version}"
    hint = "  type a task, /help for commands, Esc to interrupt"
    if color:
        status = f"{DIM}{status}{RESET}"
        hint = f"{DIM}{hint}{RESET}"
    return f"{banner}\n\n{status}\n{hint}"
