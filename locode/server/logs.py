"""Reading the model server's own log — for when it fails without answering.

mlx_lm.server loads a model lazily, inside the thread that serves the first
completion request. Anything that goes wrong there (an architecture it has no
loader for, an OOM, corrupt weights) raises *in that thread*: the thread dies,
the HTTP request is never answered, and `/v1/models` keeps returning 200. From
the client there is nothing to see but silence, so the only place the real cause
exists is the server's stderr, which locode already tees to `mlx-server.log`.

The read is offset-based rather than timestamp-based on purpose: tracebacks
carry no timestamps of their own, so "what was appended since I sent my
request" is the only precise way to attribute a failure to this request.
"""

from __future__ import annotations

import re
from pathlib import Path

from locode.config import STATE_DIR

SERVER_LOG = STATE_DIR / "mlx-server.log"

# Read no more than this from the tail; a load failure appends a few KB, while
# the log itself grows into the megabytes over a long session.
_MAX_READ = 64 * 1024

# The last line of a Python traceback ("ValueError: Model type ... not
# supported"), which is the line that actually names the cause.
_EXC_LINE = re.compile(r"^\s*(?:\w+\.)*(\w*(?:Error|Exception|Exit)): (.+)$")


def mark(log: Path | None = None) -> int:
    """The log's current size, to be handed back to `fatal_since` later."""
    try:
        return (log or SERVER_LOG).stat().st_size
    except OSError:
        return 0


def fatal_since(offset: int, log: Path | None = None) -> str:
    """The server's own explanation for a failure logged after `offset`.

    Returns "" when nothing was appended, when the appended text holds no
    exception, or when the log can't be read — an absent explanation must never
    invent one, and the caller still has "the server went silent" to report.
    """
    path = log or SERVER_LOG
    try:
        size = path.stat().st_size
        if size <= offset:
            return ""
        with open(path, "rb") as fh:
            # A restarted server truncates the log; then `offset` is past the
            # end and the honest window is the whole (short) file.
            fh.seek(max(offset, size - _MAX_READ) if size > offset else 0)
            text = fh.read(_MAX_READ).decode("utf-8", "replace")
    except OSError:
        return ""
    if "Traceback" not in text and "Exception" not in text:
        return ""
    for line in reversed(text.splitlines()):
        m = _EXC_LINE.match(line)
        if m:
            return f"{m.group(1)}: {m.group(2)}"[:300]
    return ""
