"""Shared test setup.

The offline guard is always on: any attempt to reach a non-loopback address during the test run
raises NonLoopbackBlocked before a single packet leaves the machine.
"""

from __future__ import annotations

import ipaddress
import socket
import sys
from collections.abc import Iterator
from typing import Any

import pytest

_LOCAL_NAMES = {"localhost"}


class NonLoopbackBlocked(RuntimeError):
    """Raised by the offline guard when code tries to reach a non-loopback address."""


def is_loopback_target(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.lower() in _LOCAL_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return ip.is_loopback


def _check_address(address: object, action: str) -> None:
    # Non-tuple addresses (e.g. AF_UNIX paths) never leave the machine.
    if isinstance(address, tuple) and not is_loopback_target(address[0]):
        raise NonLoopbackBlocked(f"offline guard: {action} to {address!r} is not allowed")


@pytest.fixture(scope="session", autouse=True)
def offline_guard() -> Iterator[None]:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: Any) -> None:
        _check_address(address, "connect")
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        _check_address(address, "connect")
        return real_connect_ex(self, address)

    def sendto(self: socket.socket, *args: Any) -> int:
        _check_address(args[-1], "sendto")
        return real_sendto(self, *args)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and not is_loopback_target(host):
            raise NonLoopbackBlocked(f"offline guard: name lookup for {host!r} is not allowed")
        return real_getaddrinfo(host, *args, **kwargs)

    patch = pytest.MonkeyPatch()
    patch.setattr(socket.socket, "connect", connect)
    patch.setattr(socket.socket, "connect_ex", connect_ex)
    patch.setattr(socket.socket, "sendto", sendto)
    patch.setattr(socket, "getaddrinfo", getaddrinfo)

    if sys.platform == "win32":
        # asyncio's Windows event loop connects through IOCP, not socket.connect.
        from asyncio import windows_events

        real_iocp_connect = windows_events.IocpProactor.connect

        def iocp_connect(self: Any, conn: Any, address: Any) -> Any:
            _check_address(address, "connect")
            return real_iocp_connect(self, conn, address)

        patch.setattr(windows_events.IocpProactor, "connect", iocp_connect)

    yield
    patch.undo()
