from __future__ import annotations

import socket

import pytest

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.net import loopback


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.8.9.10", "localhost", "LOCALHOST", "::1", "[::1]", "::ffff:127.0.0.1"],
)
def test_loopback_hosts_accepted(host: str) -> None:
    assert loopback.is_loopback_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "",
        "0.0.0.0",
        "192.168.1.10",
        "10.0.0.1",
        "8.8.8.8",
        "example.com",
        "127.0.0.1.evil.com",
        "localhost.evil.com",
        "localhost.",
        "127.1",
        "2130706433",
        "0x7f000001",
        "::",
        "::ffff:8.8.8.8",
        "fe80::1",
    ],
)
def test_non_loopback_hosts_rejected(host: str) -> None:
    assert not loopback.is_loopback_host(host)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:1234/v1",
        "http://localhost:1234/v1",
        "http://[::1]:1234/v1",
        "https://127.0.0.1/v1",
    ],
)
def test_local_urls_accepted(url: str) -> None:
    assert loopback.validate_local_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://192.168.1.20:1234/v1",
        "http://0.0.0.0:1234/v1",
        "http://127.0.0.1@evil.com/v1",
        "http://user:pw@127.0.0.1:1234/v1",
        "http://127.0.0.1.evil.com/v1",
        "http://evil.com#@127.0.0.1/",
        "http://evil.com/?x=127.0.0.1",
        "http://127.0.0.1\\@evil.com/",
        "http://127.0.0.1 .evil.com/",
        "http://127.0.0.1:notaport/v1",
        "ftp://127.0.0.1/",
        "file:///C:/models",
        "127.0.0.1:1234",
        "//127.0.0.1:1234/v1",
        "http:///v1",
        "",
    ],
)
def test_non_local_urls_rejected(url: str) -> None:
    with pytest.raises(DarkroomError) as caught:
        loopback.validate_local_url(url)
    assert caught.value.code is ErrorCode.NON_LOCAL_ENDPOINT


def test_open_tcp_connects_to_local_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        client = loopback.open_tcp("127.0.0.1", port, timeout_s=5)
        with client:
            conn, _ = server.accept()
            conn.close()
            assert client.gettimeout() == 5


def test_open_tcp_refuses_non_local_host() -> None:
    with pytest.raises(DarkroomError) as caught:
        loopback.open_tcp("192.168.1.20", 80, timeout_s=1)
    assert caught.value.code is ErrorCode.NON_LOCAL_ENDPOINT


def test_open_tcp_raises_oserror_when_nothing_listens() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    with pytest.raises(OSError):
        loopback.open_tcp("127.0.0.1", free_port, timeout_s=2)


def test_remapped_localhost_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", 1234))]

    monkeypatch.setattr(loopback.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(DarkroomError) as caught:
        loopback.resolve_loopback("localhost", 1234)
    assert caught.value.code is ErrorCode.NON_LOCAL_ENDPOINT
