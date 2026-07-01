"""DNS resolution for `find ip --resolve-fqdn`.

A `Resolver` maps an FQDN to the set of IP-address strings it resolves to.
The abstraction exists so the `find` engine can be driven by a fake in tests
(no network, deterministic) while production uses a real, cached,
timeout-bounded lookup over the standard library.

Contract: resolution *failure* (NXDOMAIN, timeout, no records) is reported as
the empty set, never an exception — the caller then simply skips that object.
A live name never has zero A/AAAA records, so "empty" unambiguously means
"could not resolve", which is exactly the signal `find` wants.
"""

from __future__ import annotations

import concurrent.futures
import socket
from typing import Protocol


class Resolver(Protocol):
    """Callable mapping an FQDN to its resolved IP strings (empty on failure)."""

    def __call__(self, fqdn: str) -> set[str]: ...


def _getaddrinfo_lookup(fqdn: str, *, timeout: float) -> set[str]:
    """Resolve `fqdn` to its A/AAAA addresses via the stdlib, bounded by
    `timeout`. Any resolution error collapses to the empty set so a single dead
    name can never abort a whole `find`.

    The lookup runs in a worker thread whose result is awaited with a timeout,
    rather than mutating the process-global ``socket.setdefaulttimeout`` — the
    latter is not thread-safe and would race if a concurrent frontend (the
    workbench TUI) ever resolves in parallel. A hung DNS server therefore can't
    block `find` past `timeout`."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(socket.getaddrinfo, fqdn, None, proto=socket.IPPROTO_TCP)
        try:
            infos = future.result(timeout=timeout)
        except (OSError, concurrent.futures.TimeoutError):
            return set()
    # sockaddr[0] is the numeric host string for both AF_INET and AF_INET6; the
    # stdlib types it as a union, so pin it back to str. Strip any IPv6 scope
    # suffix (e.g. `fe80::1%eth0`) so the value canonicalizes like a plain IP.
    return {str(info[4][0]).split("%", 1)[0] for info in infos}


class CachingResolver:
    """A `Resolver` that memoizes results (including failures) for the life of
    the instance, so each unique FQDN is looked up at most once per run."""

    def __init__(self, *, lookup: Resolver | None = None, timeout: float = 3.0) -> None:
        # Default to the real stdlib lookup; tests inject a fake `lookup`.
        self._lookup: Resolver = lookup or (lambda fqdn: _getaddrinfo_lookup(fqdn, timeout=timeout))
        self._cache: dict[str, set[str]] = {}

    def __call__(self, fqdn: str) -> set[str]:
        cached = self._cache.get(fqdn)
        if cached is not None:
            return cached
        result = self._lookup(fqdn)
        self._cache[fqdn] = result
        return result


def default_resolver() -> Resolver:
    """The production resolver: cached, stdlib-backed, timeout-bounded."""
    return CachingResolver()
