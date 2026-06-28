"""Interactive REPL: splash, prompt, streaming output, Esc/Ctrl-C interrupt,
slash commands, the permission / multiple-choice prompts, and the assorted UX
polish (wait spinner, diff-preview approvals, markdown styling, status toolbar,
slash completion, per-turn timing, multiline input, friendly errors).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from locode import __version__
from locode.agent.loop import AgentLoop
from locode.config import HISTORY_PATH, STATE_DIR
from locode.permissions import PermissionPolicy
from locode.ui import banner, choice, editor, render, slash
from locode.ui.interrupt import RawWriter, interrupt_scope
from locode.ui.spinner import Spinner

_PROMPT_STYLE = Style.from_dict({
    "arrow": "bold ansicyan",
    "edge": "#6c6c6c",
    "model": "ansibrightblack",
    "bottom-toolbar": "noreverse bg:default #6c6c6c",
})


class _SlashCompleter(Completer):
    """Completes slash-command names at the start of the line (before any arg)."""
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        word = text[1:]
        for name in slash.command_names():
            if name.startswith(word):
                yield Completion(name, start_position=-len(word),
                                 display=f"/{name}", display_meta=slash.describe(name))


class Repl:
    def __init__(self, config, client, manager, registry, *, yolo=False):
        self._cfg = config
        self._client = client
        self._manager = manager
        self._registry = registry
        self._policy = PermissionPolicy(config.permissions, yolo=yolo)
        self._writer = RawWriter()
        self._color = render.should_color()
        self._sink = render.StreamSink(self._writer.write,
                                       markdown=config.ui.markdown and self._color)
        self._spinner = Spinner(enabled=config.ui.spinner and self._color)
        self._last_prompt = ""
        self._server_up = False
        self._turn_chars = 0
        self._loop = AgentLoop(
            client, manager, registry, self._policy, config,
            cwd=str(Path.cwd()),
            on_delta=self._on_delta,
            on_event=self._on_event,
            confirm=self._confirm,
            select=choice.select,
            interrupt=lambda: interrupt_scope(self._loop.cancel, self._writer),
        )

    # --- public ----------------------------------------------------------
    async def run(self, splash: bool = True) -> int:
        self._server_up = await self._manager.is_up()
        if splash:
            print(banner.render(self._loop.model_alias, self._server_up,
                                self._loop._cwd, __version__, color=self._color))
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        session: PromptSession = PromptSession(
            history=FileHistory(str(HISTORY_PATH)), style=_PROMPT_STYLE,
            completer=_SlashCompleter(), complete_while_typing=True,
            multiline=True, key_bindings=self._keybindings(),
            bottom_toolbar=self._toolbar,
            prompt_continuation=lambda width, line_no, wrap: HTML("<edge>│</edge> "))
        while True:
            # Enclose the input area in a box: a titled top rule, a │ left edge on
            # each input line, and a bottom rule printed once the line is
            # submitted (so the enclosure persists in scrollback).
            w = self._term_width()
            print("\n" + render.rule(w, lead="╭", label=self._loop.model_alias,
                                      color=self._color))
            try:
                with patch_stdout():
                    line = await session.prompt_async(self._prompt(), style=_PROMPT_STYLE)
            except (EOFError, KeyboardInterrupt):
                print(render.rule(w, lead="╰", color=self._color))
                print("bye")
                return 0
            print(render.rule(w, lead="╰", color=self._color))
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if await self._slash(line):
                    return 0
                continue
            await self._turn(line)

    # --- turn ------------------------------------------------------------
    async def _turn(self, text: str) -> None:
        self._last_prompt = text
        self._turn_chars = 0
        t0 = time.monotonic()
        try:
            result = await self._loop.run_turn(text)
        except Exception as e:  # surface model/server errors without crashing
            self._server_up = not _is_conn_error(e)
            print("\n" + self._format_error(e))
            return
        finally:
            self._spinner.stop()  # never let the wait spinner leak into the prompt
        self._server_up = True
        elapsed = time.monotonic() - t0
        if result and result not in ("", None):
            if result.startswith(("⛔", "⏹")):
                print(f"\n{result}")
            else:
                print()  # finish the streamed line
        if self._cfg.ui.timing and self._turn_chars > 0:
            print(render.format_timing(self._turn_chars, elapsed, color=self._color))

    # --- prompt / toolbar / keys -----------------------------------------
    def _term_width(self) -> int:
        return shutil.get_terminal_size((80, 24)).columns

    def _prompt(self):
        # The │ left edge ties the input line to the top/bottom box rules.
        return HTML("<edge>│</edge> <arrow>❯</arrow> ")

    def _toolbar(self):
        up = "● up" if self._server_up else "○ down"
        toks = sum(len(m.get("content") or "") for m in self._loop.history) // 4
        ctx = f"{toks / 1000:.1f}k" if toks >= 1000 else str(toks)
        return (f" {up} · ctx ~{ctx} · {Path(self._loop._cwd).name} · "
                f"Esc+Enter newline · /help ")

    def _keybindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _submit(event):
            event.current_buffer.validate_and_handle()

        @kb.add("escape", "enter")
        def _newline(event):
            event.current_buffer.insert_text("\n")

        return kb

    # --- callbacks -------------------------------------------------------
    def _on_delta(self, piece: str) -> None:
        if self._spinner.active:      # first token arrived -> drop the spinner
            self._spinner.stop()
        self._turn_chars += len(piece)
        self._sink.feed(piece)

    def _on_event(self, event: dict) -> None:
        phase = event.get("phase")
        if phase == "busy_start":
            self._spinner.start(event.get("text", "working…"))
        elif phase == "busy_stop":
            self._spinner.stop()
        elif phase == "assistant_start":
            self._sink.reset()
            self._spinner.start("thinking…")
        elif phase == "assistant_end":
            self._spinner.stop()
            self._sink.flush()
        elif phase == "run":
            self._spinner.stop()
            print("\n" + render.format_run(event["name"], event.get("args", {}),
                                            color=self._color))
        elif phase == "result":
            print(render.format_result(event["name"], event.get("content", ""),
                                        event.get("error", False), color=self._color))
        elif phase == "denied":
            print(render.format_denied(event["name"], color=self._color))
        elif phase == "nudge":
            print(render.format_nudge(event.get("reason", ""), color=self._color))
        elif phase == "info":
            print(render.format_nudge(event.get("text", ""), color=self._color))

    async def _confirm(self, name: str, args: dict, preview: str) -> str:
        change = render.format_change(name, args, self._loop._cwd, color=self._color)
        if change:
            print(change)   # show the actual diff before asking to approve it
        q = f"Allow {name}?  {preview}"
        opts = ["yes (once)", "always (session)", "no", "no (always)"]
        ans = await choice.select(q, opts)
        return {"yes (once)": "yes", "always (session)": "always",
                "no": "no", "no (always)": "no_always"}.get(ans, "no")

    def _format_error(self, e: Exception) -> str:
        if _is_conn_error(e):
            base = self._cfg.base_url
            if self._cfg.server.is_managed():
                hint = "locode can start a local one — is mlx-lm installed and on PATH?"
            else:
                hint = "it's a remote endpoint — make sure the server is running there."
            return render.error(f"can't reach the model server at {base} — {hint}",
                                color=self._color)
        return render.error(str(e), color=self._color)

    # --- slash -----------------------------------------------------------
    async def _slash(self, line: str) -> bool:
        """Handle a slash command. Returns True to quit."""
        parsed = slash.parse(line)
        if not parsed:
            return False
        cmd, rest = parsed
        if cmd in ("quit", "exit", "q"):
            print("bye")
            return True
        if cmd == "help":
            print(slash.help_text())
        elif cmd == "retry":
            text = rest or self._last_prompt
            if not text:
                print("(nothing to retry yet)")
            else:
                await self._turn(text)
        elif cmd == "models":
            served = await self._manager.list_served()
            print("served: " + (", ".join(served) or "(none)"))
            known = self._manager.known_aliases()
            print("known:  " + (", ".join(known) or "(none — configure [aliases] in config.toml)"))
        elif cmd == "model":
            await self._slash_model(rest)
        elif cmd == "server":
            await self._slash_server(rest)
        elif cmd == "diff":
            await self._slash_diff(rest)
        elif cmd == "clear":
            self._loop.reset_context()
            print("(context cleared)")
        elif cmd == "permissions":
            for t, d in self._cfg.permissions.tools.items():
                print(f"  {t}: {d}")
        elif cmd == "cwd":
            if rest:
                self._loop._cwd = rest
            print(f"cwd: {self._loop._cwd}")
        elif cmd == "open":
            ed = editor.resolve_editor(self._cfg.editor)
            if ed and rest:
                await editor.open_path(ed, rest, wait=False)
            else:
                print("usage: /open <path> (and set $EDITOR)")
        else:
            print(f"unknown command: /{cmd} (try /help)")
        return False

    async def _slash_model(self, rest: str) -> None:
        if not rest:
            print(f"model: {self._loop.model_alias}")
            return
        try:
            self._manager.resolve(rest)  # validate before any action
        except KeyError:
            known = ", ".join(self._manager.known_aliases()) or "(none configured)"
            print(f"unknown model {rest!r}. known aliases: {known} "
                  "(or a full org/model id)")
            return
        self._spinner.start(f"switching to {rest}…")
        try:
            mid = await self._manager.switch(rest)
            self._loop.set_model(rest)  # only on success
        except Exception as e:
            self._spinner.stop()
            print(self._format_error(e))
            return
        self._spinner.stop()
        self._server_up = True
        print(f"now serving {mid}")

    async def _slash_server(self, rest: str) -> None:
        if rest == "restart":
            self._spinner.start("restarting server…")
            try:
                await self._manager.switch(self._loop.model_alias)
            finally:
                self._spinner.stop()
            self._server_up = True
            print("restarted")
        elif rest == "stop":
            await self._manager.stop()
            self._server_up = False
            print("stopped")
        else:
            st = await self._manager.status()
            self._server_up = st.up
            print(f"server: {'up' if st.up else 'down'}  {st.model_id or ''}")

    async def _slash_diff(self, rest: str) -> None:
        import asyncio
        argv = ["git", "diff", "--no-color"] + ([rest] if rest else [])
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=self._loop._cwd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await proc.communicate()
        except OSError as e:
            print(f"diff failed: {e}")
            return
        print(out.decode("utf-8", "replace").strip() or "(no changes)")


def _is_conn_error(e: Exception) -> bool:
    return isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError))
