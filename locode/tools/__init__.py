"""Tool package + default registry assembly."""

from __future__ import annotations

from locode.tools.ask import AskUser
from locode.tools.base import Registry
from locode.tools.fs import all_tools as _fs_tools
from locode.tools.plan import UpdatePlan
from locode.tools.shell import Bash
from locode.tools.web import WebFetch, WebSearch, build_search_backends


def build_registry(config=None) -> Registry:
    reg = Registry()
    for tool in _fs_tools():      # read_file, ls, glob, grep, write_file, edit_file, move_file
        reg.register(tool)
    reg.register(Bash())
    reg.register(AskUser())
    reg.register(UpdatePlan())
    # Web tools are always registered (so the model knows the capability exists);
    # web_search self-disables with an actionable error when no key is set.
    web = getattr(config, "web", None)
    if web is not None:
        reg.register(WebSearch(build_search_backends(web),
                               provider=web.search_provider,
                               max_results=web.max_results))
        reg.register(WebFetch(web.fetch_allowlist, max_bytes=web.fetch_max_bytes,
                              timeout=web.fetch_timeout))
    return reg
