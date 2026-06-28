import socket

import httpx
import pytest

from locode.tools.base import ToolContext
from locode.tools.web import (BraveBackend, DuckDuckGoBackend, GuardError,
                              TavilyBackend, WebFetch, WebSearch,
                              build_search_backends, host_allowed, is_public,
                              validate_url)
import ipaddress

_DDG_HTML = """
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpython&amp;rut=abc">Python <b>site</b></a>
  <a class="result__snippet" href="x">A <b>great</b> page   about python.</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org">Docs</a>
  <a class="result__snippet">Reference.</a>
</div>
"""

ALLOW = ["example.com", "docs.python.org"]


def _resolver(ip):
    """Return a getaddrinfo-shaped stub yielding `ip` for any host."""
    def res(host, port, proto=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    return res


def _ctx():
    return ToolContext(cwd=".")


# --- guard units --------------------------------------------------------------
def test_host_allowed_suffix_and_exact():
    assert host_allowed("example.com", ALLOW)
    assert host_allowed("docs.python.org", ALLOW)
    assert not host_allowed("evilexample.com", ALLOW)   # not a dotted suffix
    assert not host_allowed("example.com.evil.com", ALLOW)


@pytest.mark.parametrize("ip,public", [
    ("93.184.216.34", True),
    ("10.0.0.1", False),
    ("127.0.0.1", False),
    ("169.254.169.254", False),     # cloud metadata
    ("192.168.1.1", False),
    ("::1", False),
])
def test_is_public(ip, public):
    assert is_public(ipaddress.ip_address(ip)) is public


def test_ipv4_mapped_loopback_is_not_public():
    assert is_public(ipaddress.ip_address("::ffff:127.0.0.1")) is False


def test_validate_rejects_bad_scheme():
    with pytest.raises(GuardError):
        validate_url("ftp://example.com/x", ALLOW, _resolver("93.184.216.34"))


def test_validate_rejects_userinfo():
    with pytest.raises(GuardError):
        validate_url("http://example.com@evil.com/", ALLOW, _resolver("93.184.216.34"))


def test_validate_rejects_offlist_host():
    with pytest.raises(GuardError):
        validate_url("https://evil.com/", ALLOW, _resolver("93.184.216.34"))


def test_validate_fails_closed_on_empty_allowlist():
    with pytest.raises(GuardError):
        validate_url("https://example.com/", [], _resolver("93.184.216.34"))


def test_validate_refuses_private_resolution():
    with pytest.raises(GuardError):
        validate_url("https://example.com/", ALLOW, _resolver("10.0.0.1"))


def test_validate_happy_path_pins_ip():
    host, port, ip, scheme = validate_url(
        "https://example.com/a", ALLOW, _resolver("93.184.216.34"))
    assert (host, port, ip, scheme) == ("example.com", 443, "93.184.216.34", "https")


# --- WebFetch -----------------------------------------------------------------
async def test_web_fetch_refused_offlist():
    tool = WebFetch(ALLOW, resolver=_resolver("93.184.216.34"))
    res = await tool.run({"url": "https://evil.com/"}, _ctx())
    assert res.is_error and "not on the allowlist" in res.content


async def test_web_fetch_happy_path():
    def handler(request):
        return httpx.Response(200, text="<html>hello</html>")
    tool = WebFetch(ALLOW, transport=httpx.MockTransport(handler),
                    resolver=_resolver("93.184.216.34"))
    res = await tool.run({"url": "https://example.com/page"}, _ctx())
    assert not res.is_error
    assert "hello" in res.content and "200 example.com/page" in res.content


async def test_web_fetch_size_cap():
    big = "x" * 1000
    def handler(request):
        return httpx.Response(200, text=big)
    tool = WebFetch(ALLOW, max_bytes=100, transport=httpx.MockTransport(handler),
                    resolver=_resolver("93.184.216.34"))
    res = await tool.run({"url": "https://example.com/big"}, _ctx())
    # body (after the "head\n\n") truncated to the cap
    body = res.content.split("\n\n", 1)[1]
    assert body == "x" * 100


# --- web search backends ------------------------------------------------------
def _tavily(transport, key="k"):
    return TavilyBackend(key, transport=transport)


def _brave(transport, key="k"):
    return BraveBackend(key, transport=transport)


async def test_tavily_backend_normalizes():
    def handler(request):
        assert request.url.host == "api.tavily.com"
        return httpx.Response(200, json={"results": [
            {"title": "Python", "url": "https://python.org", "content": "the   language"},
        ]})
    out = await _tavily(httpx.MockTransport(handler)).search("python", 5)
    assert out[0].title == "Python" and out[0].url == "https://python.org"
    assert out[0].snippet == "the language"   # whitespace collapsed


async def test_duckduckgo_backend_parses_html():
    def handler(request):
        assert request.url.host == "html.duckduckgo.com"
        return httpx.Response(200, text=_DDG_HTML)
    out = await DuckDuckGoBackend(transport=httpx.MockTransport(handler)).search("python", 5)
    assert len(out) == 2
    assert out[0].title == "Python site"
    assert out[0].url == "https://example.com/python"     # uddg-unwrapped
    assert out[0].snippet == "A great page about python."  # tags stripped, ws collapsed
    assert out[1].url == "https://docs.python.org"


def test_duckduckgo_always_enabled():
    assert DuckDuckGoBackend().enabled() is True


async def test_brave_backend_normalizes():
    def handler(request):
        assert request.url.host == "api.search.brave.com"
        assert request.headers.get("X-Subscription-Token") == "k"
        return httpx.Response(200, json={"web": {"results": [
            {"title": "Rust", "url": "https://rust-lang.org", "description": "systems lang"},
        ]}})
    out = await _brave(httpx.MockTransport(handler)).search("rust", 5)
    assert out[0].title == "Rust" and out[0].url == "https://rust-lang.org"
    assert out[0].snippet == "systems lang"


# --- WebSearch tool (provider selection) --------------------------------------
async def test_web_search_disabled_when_only_keyed_backends_and_no_key():
    # Without the keyless DDG default, auto with no keys is an actionable error.
    backends = [TavilyBackend(""), BraveBackend("")]
    res = await WebSearch(backends, provider="auto").run({"query": "x"}, _ctx())
    assert res.is_error
    assert "TAVILY_API_KEY" in res.content and "BRAVE_API_KEY" in res.content


async def test_web_search_auto_falls_back_to_duckduckgo():
    # Default config: no keys -> auto should land on the keyless DDG backend.
    def handler(request):
        assert request.url.host == "html.duckduckgo.com"
        return httpx.Response(200, text=_DDG_HTML)
    tr = httpx.MockTransport(handler)
    tool = WebSearch([TavilyBackend(""), BraveBackend(""),
                      DuckDuckGoBackend(transport=tr)], provider="auto")
    res = await tool.run({"query": "python"}, _ctx())
    assert not res.is_error and "[duckduckgo]" in res.content and "1. Python site" in res.content


async def test_web_search_auto_prefers_keyed_over_duckduckgo():
    def handler(request):
        assert request.url.host == "api.tavily.com"   # not DDG
        return httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://t", "content": "paid"}]})
    tr = httpx.MockTransport(handler)
    tool = WebSearch([TavilyBackend("k", transport=tr), BraveBackend(""),
                      DuckDuckGoBackend(transport=tr)], provider="auto")
    res = await tool.run({"query": "q"}, _ctx())
    assert not res.is_error and "[tavily]" in res.content


async def test_web_search_auto_picks_first_enabled():
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://t", "content": "via tavily"}]})
    tr = httpx.MockTransport(handler)
    tool = WebSearch([TavilyBackend("k", transport=tr), BraveBackend("", transport=tr)],
                     provider="auto")
    res = await tool.run({"query": "q"}, _ctx())
    assert not res.is_error and "[tavily]" in res.content and "1. T" in res.content


async def test_web_search_explicit_provider_selects_brave():
    def handler(request):
        assert request.url.host == "api.search.brave.com"
        return httpx.Response(200, json={"web": {"results": [
            {"title": "B", "url": "https://b", "description": "via brave"}]}})
    tr = httpx.MockTransport(handler)
    # Tavily also has a key, but provider="brave" must override auto-ordering.
    tool = WebSearch([TavilyBackend("k", transport=tr), BraveBackend("k", transport=tr)],
                     provider="brave")
    res = await tool.run({"query": "q"}, _ctx())
    assert not res.is_error and "[brave]" in res.content and "1. B" in res.content


async def test_web_search_explicit_provider_disabled_names_its_env():
    tool = WebSearch([TavilyBackend("k"), BraveBackend("")], provider="brave")
    res = await tool.run({"query": "q"}, _ctx())
    assert res.is_error and "BRAVE_API_KEY" in res.content


async def test_web_search_unknown_provider():
    tool = WebSearch(build_search_backends(_FakeWeb("k", "k")), provider="bing")
    res = await tool.run({"query": "q"}, _ctx())
    assert res.is_error and "unknown search provider" in res.content


class _FakeWeb:
    def __init__(self, tavily="", brave=""):
        self.tavily_api_key = tavily
        self.brave_api_key = brave
