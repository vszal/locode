"""Web tools: `web_search` (Tavily) and `web_fetch` (allowlist + SSRF guard).

Ported from the offload repo's `tools/url_guard.py` + `web_fetch.sh`. Web egress
is the model's main exfiltration/SSRF surface, so `web_fetch` is made safe by
deterministic guards rather than a human prompt:

  1. scheme must be http/https; no `user@host` userinfo (a classic allowlist
     bypass: http://allowed.com@evil.com/).
  2. host must match the configured allowlist (exact or dotted-suffix). Empty
     allowlist => deny everything (fail closed).
  3. the host is resolved HERE and EVERY returned address must be public —
     private / loopback / link-local / reserved / multicast / unspecified are
     refused (covers 127/8, 10/8, 172.16/12, 192.168/16, 169.254/16 incl. the
     cloud-metadata IP, ::1, fc00::/7, fe80::/10, IPv4-mapped IPv6, ...).
  4. the connection is pinned to the validated IP (no DNS-rebinding race) and
     redirects are NOT followed (a 3xx can't smuggle past the check after).

`web_search` is direct (Tavily HTTPS), gated by the permission prompt (ASK by
default) so a query is shown before a credit is spent; with no key it stays
registered but returns an actionable "set TAVILY_API_KEY" error.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx

from locode.tools.base import ToolContext, ToolResult


# --- SSRF + allowlist guard ---------------------------------------------------
class GuardError(Exception):
    """Raised when a URL fails the web_fetch policy (message is user-facing)."""


def parse_allowlist(hosts: list[str]) -> list[str]:
    return [h.strip().lower().lstrip(".") for h in hosts if h and h.strip()]


def host_allowed(host: str, allow: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in allow)


def is_public(ip: ipaddress._BaseAddress) -> bool:
    # Decode IPv4-mapped IPv6 (::ffff:a.b.c.d) before judging, else a mapped
    # loopback could read as "global".
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ipaddress.ip_address(ip.ipv4_mapped)
    return ip.is_global and not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def validate_url(url: str, allow: list[str], resolver=socket.getaddrinfo):
    """Validate `url` against the fetch policy. On success returns
    (host, port, pinned_ip, scheme). Raises GuardError otherwise. `resolver` is
    injectable so tests never touch the network."""
    p = urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise GuardError(f"scheme must be http/https (got {p.scheme!r})")
    if "@" in p.netloc:
        raise GuardError("userinfo (@) in the authority is not allowed")
    host = (p.hostname or "").lower()
    if not host:
        raise GuardError("no host in URL")
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        raise GuardError("invalid port")
    if not allow:
        raise GuardError("fetch allowlist is empty (fail closed)")
    if not host_allowed(host, allow):
        raise GuardError(f"host {host!r} is not on the allowlist {allow}")
    try:
        infos = resolver(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise GuardError(f"DNS resolution failed: {e}")
    pinned = None
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not is_public(addr):
            raise GuardError(f"{host} resolves to non-public address {addr}")
        if pinned is None:
            pinned = info[4][0]
    if pinned is None:
        raise GuardError(f"no usable address for {host}")
    return host, port, pinned, p.scheme


def _pin_url(url: str, pinned_ip: str) -> str:
    """Rewrite the authority to the validated IP so the TCP connection can't be
    steered elsewhere by a DNS rebind. The original host is preserved for the
    Host header + TLS SNI by the caller."""
    p = urlsplit(url)
    host = pinned_ip
    if ":" in pinned_ip and not pinned_ip.startswith("["):
        host = f"[{pinned_ip}]"  # bracket IPv6 literals
    netloc = f"{host}:{p.port}" if p.port else host
    return urlunsplit((p.scheme, netloc, p.path or "/", p.query, p.fragment))


# --- tools --------------------------------------------------------------------
class WebFetch:
    name = "web_fetch"
    description = ("Fetch a single http(s) URL (allowlisted public hosts only). "
                  "Returns the response body as text; does not follow redirects.")
    permission = "auto"  # bounded by the guard, not a prompt
    schema = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Absolute http(s) URL."}},
        "required": ["url"],
    }

    def __init__(self, allowlist: list[str], *, max_bytes: int = 5_000_000,
                 timeout: float = 20.0, transport: httpx.AsyncBaseTransport | None = None,
                 resolver=socket.getaddrinfo):
        self._allow = parse_allowlist(allowlist)
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._transport = transport
        self._resolver = resolver

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        url = str(args.get("url", "")).strip()
        if not url:
            return ToolResult("web_fetch: no url given", is_error=True)
        try:
            host, port, ip, scheme = validate_url(url, self._allow, self._resolver)
        except GuardError as e:
            return ToolResult(f"web_fetch refused: {e}", is_error=True)

        target = _pin_url(url, ip)
        host_header = host if port in (80, 443) else f"{host}:{port}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport,
                                         follow_redirects=False) as c:
                # Pin the TCP target to `ip` (target URL) but keep the real
                # hostname for TLS SNI + certificate verification.
                req = c.build_request(
                    "GET", target,
                    headers={"Host": host_header, "User-Agent": "locode/web_fetch"},
                    extensions={"sni_hostname": host} if scheme == "https" else {})
                resp = await c.send(req, stream=True)
                try:
                    chunks, total = [], 0
                    async for chunk in resp.aiter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > self._max_bytes:
                            break
                finally:
                    await resp.aclose()
        except httpx.HTTPError as e:
            return ToolResult(f"web_fetch failed: {e}", is_error=True)

        body = b"".join(chunks)[: self._max_bytes].decode("utf-8", errors="replace")
        status = resp.status_code
        head = f"{status} {host}{urlsplit(url).path}  ({len(body)} bytes)"
        if status >= 400:
            return ToolResult(f"{head}\n{body}", is_error=True)
        return ToolResult(f"{head}\n\n{body}")


# --- web search: pluggable backends -------------------------------------------
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def _snippet(text: str) -> str:
    return " ".join((text or "").split())[:200]


class SearchBackend:
    """A web-search provider. Subclasses normalize their API response to a list
    of SearchResult. `name` selects it in config; `key_env` names the env var
    quoted in the "disabled" hint when no key is set."""
    name = "base"
    key_env = ""

    def enabled(self) -> bool:
        raise NotImplementedError

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        raise NotImplementedError


class TavilyBackend(SearchBackend):
    name = "tavily"
    key_env = "TAVILY_API_KEY"
    _URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str, *, timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._key = api_key or ""
        self._timeout = timeout
        self._transport = transport

    def enabled(self) -> bool:
        return bool(self._key)

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        payload = {"api_key": self._key, "query": query,
                   "max_results": max_results, "search_depth": "basic"}
        async with httpx.AsyncClient(timeout=self._timeout,
                                     transport=self._transport) as c:
            r = await c.post(self._URL, json=payload)
            r.raise_for_status()
            data = r.json()
        return [SearchResult((x.get("title") or "").strip(),
                             (x.get("url") or "").strip(),
                             _snippet(x.get("content")))
                for x in (data.get("results") or [])]


class BraveBackend(SearchBackend):
    name = "brave"
    key_env = "BRAVE_API_KEY"
    _URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, *, timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._key = api_key or ""
        self._timeout = timeout
        self._transport = transport

    def enabled(self) -> bool:
        return bool(self._key)

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        headers = {"Accept": "application/json", "X-Subscription-Token": self._key}
        params = {"q": query, "count": min(max_results, 20)}
        async with httpx.AsyncClient(timeout=self._timeout,
                                     transport=self._transport) as c:
            r = await c.get(self._URL, params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
        results = ((data.get("web") or {}).get("results")) or []
        return [SearchResult((x.get("title") or "").strip(),
                             (x.get("url") or "").strip(),
                             _snippet(x.get("description")))
                for x in results]


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo wraps result links as //duckduckgo.com/l/?uddg=<encoded-url>.
    Return the real target (parse_qs un-percent-encodes it); pass others through."""
    if href.startswith("//"):
        href = "https:" + href
    p = urlsplit(href)
    if "duckduckgo.com" in p.netloc and p.path.startswith("/l/"):
        uddg = parse_qs(p.query).get("uddg")
        if uddg:
            return uddg[0]
    return href


class _DDGParser(HTMLParser):
    """Pull (title, url, snippet) triples out of the DDG HTML results page,
    without a heavyweight HTML dependency. Result links carry class
    `result__a`; snippets carry `result__snippet`."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._cur: dict | None = None
        self._mode: str | None = None   # "title" | "snippet"
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        cls = dict(attrs).get("class", "") or ""
        if "result__a" in cls:
            self._flush()
            self._cur = {"url": _unwrap_ddg(dict(attrs).get("href", "")),
                         "title": "", "snippet": ""}
            self._mode, self._buf = "title", []
        elif "result__snippet" in cls and self._cur is not None:
            self._mode, self._buf = "snippet", []

    def handle_data(self, data):
        if self._mode:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._mode and self._cur is not None:
            self._cur[self._mode] = " ".join("".join(self._buf).split())
            self._mode = None

    def _flush(self):
        if self._cur and self._cur.get("url"):
            self.results.append(SearchResult(self._cur["title"], self._cur["url"],
                                             self._cur["snippet"][:200]))
        self._cur = None

    def finish(self) -> list[SearchResult]:
        self._flush()
        return self.results


class DuckDuckGoBackend(SearchBackend):
    """Keyless default backend — scrapes the DDG HTML endpoint. Always enabled,
    so web_search works with zero configuration; lower quality / rate-limited
    vs. the keyed providers, which is why `auto` prefers a keyed backend."""
    name = "duckduckgo"
    key_env = ""   # no key required
    _URL = "https://html.duckduckgo.com/html/"

    def __init__(self, api_key: str = "", *, timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._timeout = timeout
        self._transport = transport

    def enabled(self) -> bool:
        return True

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; locode/web_search)"}
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport,
                                     follow_redirects=True) as c:
            r = await c.post(self._URL, data={"q": query}, headers=headers)
            r.raise_for_status()
            html = r.text
        parser = _DDGParser()
        parser.feed(html)
        return parser.finish()[:max_results]


# Ordered: keyed providers first, the keyless DuckDuckGo default last — so
# `auto` uses a paid backend when configured and otherwise falls back to DDG.
_BACKENDS = {"tavily": TavilyBackend, "brave": BraveBackend,
             "duckduckgo": DuckDuckGoBackend}


def build_search_backends(web_config) -> list[SearchBackend]:
    """Construct every known backend from config (ordered, Tavily first). Each is
    always built; a backend with no key reports `enabled() is False`."""
    keys = {"tavily": getattr(web_config, "tavily_api_key", ""),
            "brave": getattr(web_config, "brave_api_key", "")}
    return [cls(keys.get(name, "")) for name, cls in _BACKENDS.items()]


class WebSearch:
    name = "web_search"
    description = ("Search the web. Returns ranked results (title, url, snippet) "
                  "to optionally web_fetch.")
    permission = "ask"
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Cap on results."},
        },
        "required": ["query"],
    }

    def __init__(self, backends: list[SearchBackend], *, provider: str = "auto",
                 max_results: int = 5):
        self._backends = list(backends)
        self._provider = (provider or "auto").lower()
        self._max_results = max_results

    def _pick(self) -> tuple[SearchBackend | None, str]:
        """Resolve the backend to use. Returns (backend_or_None, error_message)."""
        if self._provider != "auto":
            match = next((b for b in self._backends if b.name == self._provider), None)
            if match is None:
                known = ", ".join(self._backends and [b.name for b in self._backends]
                                  or _BACKENDS)
                return None, f"unknown search provider {self._provider!r} (known: {known})"
            if not match.enabled():
                return None, (f"web_search provider {match.name!r} is disabled: set "
                              f"{match.key_env} (env) or [web].{match.name}_api_key.")
            return match, ""
        enabled = [b for b in self._backends if b.enabled()]
        if enabled:
            return enabled[0], ""
        hints = " or ".join(b.key_env for b in self._backends if b.key_env) \
            or "a provider API key"
        return None, f"web_search is disabled: set {hints} to enable it."

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        backend, err = self._pick()
        if backend is None:
            return ToolResult(err, is_error=True)
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult("web_search: empty query", is_error=True)
        n = int(args.get("max_results") or self._max_results)
        try:
            results = await backend.search(query, n)
        except httpx.HTTPError as e:
            return ToolResult(f"web_search ({backend.name}) failed: {e}", is_error=True)
        if not results:
            return ToolResult(f"No results for {query!r}.")
        lines = [f"[{backend.name}] results for {query!r}:"]
        for i, res in enumerate(results[:n], 1):
            lines.append(f"{i}. {res.title}\n   {res.url}\n   {res.snippet}")
        return ToolResult("\n".join(lines))
