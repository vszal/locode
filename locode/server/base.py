"""The backend seam — everything the agent loop is allowed to know about serving.

`SingleGpuManager` runs one local `mlx_lm.server` and is the only implementation
today. M6 adds a pool that may hold several backends (local, LAN, cloud), and
the loop must not learn the difference: it asks for a *client for an alias* and
gets one, whoever is serving it.

`client_for` is the whole trick. Before this seam, `cli.py` built one
`ModelClient(cfg.base_url)` at startup and handed it to `AgentLoop`, which held
it for the session — an assumption that the endpoint is fixed and known before
the first turn. In a pool the endpoint depends on which backend holds the alias,
and that is decided per turn, so **the manager owns client construction** and
the loop asks each time. In single mode the answer is always the same object,
which is why this refactor is behaviourally invisible.

`Status` lives here rather than in `manager.py` because it is part of the
contract: an implementation has to produce one without importing the single-GPU
manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # deferred: model.client imports server.logs at runtime
    from locode.model.client import ModelClient


@dataclass
class Status:
    up: bool
    model_id: str | None = None
    base_url: str = ""


@runtime_checkable
class ModelBackendManager(Protocol):
    """What a backend manager owes the CLI, the REPL and the agent loop.

    Deliberately the *existing* public surface of `SingleGpuManager` plus
    `client_for`, so single mode satisfies it without behaviour changes and a
    pool can be swapped in behind it.
    """

    # --- naming ---------------------------------------------------------
    def resolve(self, name: str) -> str:
        """Alias (or full org/model id) -> the model id to serve."""

    def known_aliases(self) -> list[str]:
        """Aliases available now, sorted."""

    # --- the seam -------------------------------------------------------
    def client_for(self, alias: str | None = None) -> "ModelClient":
        """A `ModelClient` pointed at whichever backend serves `alias`.

        Cheap and callable per turn: `ModelClient` is a value object that opens
        its `httpx.AsyncClient` per request, so implementations are expected to
        return a cached instance rather than build a connection here. `None`
        means the configured default.
        """

    # --- lifecycle ------------------------------------------------------
    async def is_up(self, alias: str | None = None) -> bool: ...

    async def list_served(self) -> list[str]: ...

    async def status(self) -> Status: ...

    async def ensure_up(self, alias: str | None = None) -> str:
        """Guarantee `alias` is being served; returns the served model id."""

    async def start(self, alias: str) -> str: ...

    async def stop(self) -> None: ...

    async def switch(self, alias: str) -> str: ...

    async def restart(self, alias: str) -> str: ...
