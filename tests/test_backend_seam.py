"""The backend seam (M6.1): managers own client construction.

`SingleGpuManager` is the only implementation today, but the point of the
Protocol is that the loop cannot tell. These tests pin the two properties the
pool will depend on: conformance, and that `client_for` is cheap enough to call
on every model call.
"""

import pytest

from locode.config import Config
from locode.model.client import ModelClient
from locode.server.base import ModelBackendManager, Status
from locode.server.manager import SingleGpuManager


def _mgr(**server):
    cfg = Config()
    for k, v in server.items():
        setattr(cfg.server, k, v)
    return SingleGpuManager(cfg), cfg


def test_the_single_gpu_manager_satisfies_the_protocol():
    mgr, _ = _mgr()
    assert isinstance(mgr, ModelBackendManager)


def test_client_for_points_at_the_configured_endpoint():
    mgr, cfg = _mgr()
    client = mgr.client_for("qwen38")
    assert isinstance(client, ModelClient)
    assert cfg.base_url.rstrip("/").endswith(str(cfg.server.port))


def test_client_for_is_cached_so_the_loop_can_call_it_per_turn():
    """The loop asks on every model call; that must not build a client each time."""
    mgr, _ = _mgr()
    first = mgr.client_for("qwen38")
    assert mgr.client_for("qwen38") is first
    assert mgr.client_for() is first


def test_the_alias_is_ignored_in_single_mode():
    """One endpoint serves whatever is resident, so every alias gets one client.

    A pool answers differently — that difference is the whole reason the loop
    asks per turn instead of holding a client for the session.
    """
    mgr, _ = _mgr()
    assert mgr.client_for("qwen38") is mgr.client_for("qwythos9")


def test_status_is_importable_from_the_seam_not_just_the_manager():
    """Status is part of the contract: an implementation must be able to build
    one without importing the single-GPU manager."""
    s = Status(up=True, model_id="m", base_url="http://x")
    assert (s.up, s.model_id, s.base_url) == (True, "m", "http://x")
    assert Status(up=False).model_id is None


def test_the_agent_loop_no_longer_takes_a_client():
    """The seam's headline: constructing a loop must not require an endpoint."""
    import inspect

    from locode.agent.loop import AgentLoop

    params = list(inspect.signature(AgentLoop.__init__).parameters)
    assert params[1] == "manager", params
    assert "client" not in params, params
