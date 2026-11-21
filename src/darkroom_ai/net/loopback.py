"""Loopback-only networking.

This is the ONLY module in darkroom_ai that may import socket/HTTP libraries (a test enforces it).
Everything here refuses to talk to anything but this computer.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from darkroom_ai.errors import DarkroomError, ErrorCode

# Type of an open connection, so other modules can annotate it without importing socket.
Connection = socket.socket

ALLOWED_SCHEMES = ("http", "https")
_LOCAL_NAMES = frozenset({"localhost"})
_SUSPICIOUS_CHARS = frozenset("\\ \t\r\n")


def _ip_is_loopback(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return ip.is_loopback


def is_loopback_host(host: str) -> bool:
    """True for `localhost` and literal loopback IPs (127.0.0.0/8, ::1). Nothing else."""
    name = host.lower()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if name in _LOCAL_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return _ip_is_loopback(ip)


def _reject(what: str, value: str, reason: str) -> DarkroomError:
    return DarkroomError(
        ErrorCode.NON_LOCAL_ENDPOINT,
        f"{what} = {value!r} is not allowed: {reason}.",
        details={"setting": what, "value": value, "reason": reason},
    )


def validate_local_host(host: str, *, what: str = "host") -> str:
    """Return `host` if it is a loopback host, else raise NON_LOCAL_ENDPOINT."""
    if not is_loopback_host(host):
        raise _reject(what, host, "the host is not a loopback address")
    return host


def validate_local_url(url: str, *, what: str = "base_url") -> str:
    """Return `url` if it points at this computer, else raise NON_LOCAL_ENDPOINT."""
    if any(ch in _SUSPICIOUS_CHARS or ord(ch) < 32 for ch in url):
        raise _reject(what, url, "it contains whitespace, control characters or a backslash")
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError as exc:
        raise _reject(what, url, f"it is not a valid URL ({exc})") from exc
    if parts.scheme not in ALLOWED_SCHEMES:
        raise _reject(what, url, "the scheme must be http or https")
    if parts.username is not None or parts.password is not None:
        raise _reject(what, url, "user names and passwords in the URL are not allowed")
    if not parts.hostname:
        raise _reject(what, url, "it has no host")
    if not is_loopback_host(parts.hostname):
        raise _reject(what, url, "the host is not a loopback address")
    return url


def resolve_loopback(host: str, port: int) -> list[tuple[socket.AddressFamily, tuple[str, int]]]:
    """Resolve `host` and return its addresses; raise if any of them is not loopback.

    Literal IPs need no lookup. `localhost` is looked up locally and every result is checked, so a
    remapped `localhost` cannot send traffic elsewhere.
    """
    validate_local_host(host)
    infos = socket.getaddrinfo(host.strip("[]"), port, type=socket.SOCK_STREAM)
    resolved: list[tuple[socket.AddressFamily, tuple[str, int]]] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        address = str(sockaddr[0])
        if not _ip_is_loopback(ipaddress.ip_address(address.split("%")[0])):
            raise _reject("host", host, f"it resolves to the non-loopback address {address}")
        resolved.append((family, (address, int(sockaddr[1]))))
    return resolved


def open_tcp(host: str, port: int, *, timeout_s: float) -> socket.socket:
    """Open a TCP connection to a loopback address with a timeout.

    Raises DarkroomError(NON_LOCAL_ENDPOINT) for non-local hosts and OSError if nothing answers;
    callers map OSError to their own code (e.g. LR_NOT_REACHABLE).
    """
    last_error: OSError | None = None
    for family, sockaddr in resolve_loopback(host, port):
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout_s)
        try:
            sock.connect(sockaddr)
        except OSError as exc:
            sock.close()
            last_error = exc
            continue
        return sock
    raise last_error or OSError(f"no address found for {host}:{port}")


class LocalHttpError(OSError):
    """A request to the local server failed at the connection level (refused, timed out, ...)."""


class LocalHttp:
    """Minimal JSON-over-HTTP client that can only talk to this computer.

    - The base URL must be a loopback address (checked here and again for every request).
    - Environment proxy settings are ignored (trust_env=False) and no proxy is mounted, so
      HTTP_PROXY / HTTPS_PROXY / ALL_PROXY cannot route a request anywhere else.
    - Redirects are not followed.
    """

    def __init__(self, base_url: str, *, timeout_s: float) -> None:
        import httpx2

        validate_local_url(base_url)
        parts = urlsplit(base_url)
        assert parts.hostname is not None
        resolve_loopback(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))

        def only_loopback(request: httpx2.Request) -> None:
            if not is_loopback_host(request.url.host):
                raise _reject("request", str(request.url), "the host is not a loopback address")

        self._errors = (httpx2.TransportError, httpx2.InvalidURL)
        self._client = httpx2.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout_s,
            trust_env=False,
            proxy=None,
            mounts={},
            follow_redirects=False,
            transport=httpx2.HTTPTransport(trust_env=False, proxy=None, retries=0),
            event_hooks={"request": [only_loopback]},
        )

    def close(self) -> None:
        self._client.close()

    def request_json(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        *,
        timeout_s: float | None = None,
    ) -> tuple[int, object]:
        """Send a request and return (status code, decoded JSON or the body text)."""
        kwargs: dict[str, object] = {}
        if payload is not None:
            kwargs["json"] = payload
        if timeout_s is not None:
            kwargs["timeout"] = timeout_s
        try:
            response = self._client.request(method, path.lstrip("/"), **kwargs)  # type: ignore[arg-type]
        except self._errors as exc:
            raise LocalHttpError(f"{type(exc).__name__}: {exc}") from exc
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text
