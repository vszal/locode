"""Single-select multiple-choice widget, shared by model-initiated ask_user and
harness-initiated permission prompts.

Interactive path is an INLINE selector (arrow-navigable, no full-screen dialog):
it renders a few lines at the cursor, lets you move with ↑/↓ (or number keys),
Enter selects, Esc/Ctrl-C cancels, and the menu erases when done. Falls back to
a numbered stdin prompt when not on a TTY. Pure helpers (option normalization,
typed-answer parsing) are unit-tested; the live widget is not.
"""

from __future__ import annotations

import sys


def normalize_options(options: list[str]) -> list[str]:
    seen, out = set(), []
    for o in options:
        o = str(o).strip()
        if o and o not in seen:
            seen.add(o)
            out.append(o)
    return out


def parse_answer(raw: str, options: list[str]) -> str | None:
    """Map a typed answer (a 1-based number or exact text) to an option."""
    raw = raw.strip()
    if raw.isdigit():
        i = int(raw) - 1
        if 0 <= i < len(options):
            return options[i]
        return None
    for o in options:
        if raw.lower() == o.lower():
            return o
    return None


async def _inline_select(question: str, options: list[str]) -> str | None:
    """Inline arrow-key menu. Returns the chosen option, or None if cancelled."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    state = {"i": 0}

    def fragments():
        frags = [("bold", f"? {question}\n")]
        for idx, opt in enumerate(options):
            if idx == state["i"]:
                frags.append(("class:sel", f" › {idx + 1}. {opt}\n"))
            else:
                frags.append(("", f"   {idx + 1}. {opt}\n"))
        frags.append(("class:dim", "  ↑/↓ move · enter select · esc cancel"))
        return frags

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(event):
        state["i"] = (state["i"] - 1) % len(options)

    @kb.add("down")
    @kb.add("j")
    def _down(event):
        state["i"] = (state["i"] + 1) % len(options)

    @kb.add("enter")
    def _enter(event):
        event.app.exit(result=options[state["i"]])

    @kb.add("escape")
    @kb.add("c-c")
    @kb.add("c-d")
    def _cancel(event):
        event.app.exit(result=None)

    for n in range(1, min(9, len(options)) + 1):
        @kb.add(str(n))
        def _pick(event, n=n):
            event.app.exit(result=options[n - 1])

    app = Application(
        layout=Layout(HSplit([Window(FormattedTextControl(fragments),
                                     always_hide_cursor=True)])),
        key_bindings=kb,
        style=Style.from_dict({"sel": "reverse", "dim": "#888888"}),
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
    )
    return await app.run_async()


async def select(question: str, options: list[str]) -> str:
    """Present `question` with `options`; return the chosen option (or "" if
    cancelled). Never blocks the event loop on the interactive path."""
    options = normalize_options(options)
    if not options:
        return ""
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            result = await _inline_select(question, options)
        except Exception:
            result = None
        else:
            if result is not None:
                print(f"  ✓ {result}")
                return result
            print("  ✗ cancelled")
            return ""
    # Non-TTY fallback: numbered prompt (used only without an interactive TTY).
    print(f"? {question}")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    try:
        raw = input("> ")
    except EOFError:
        return ""
    ans = parse_answer(raw, options)
    return ans if ans is not None else ""
