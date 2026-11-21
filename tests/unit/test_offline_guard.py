"""Offline guard (PRD goal 5).

No non-loopback traffic during tests, and no network imports outside net/loopback.py.
"""

from __future__ import annotations

import ast
import asyncio
import socket
import tomllib
from pathlib import Path

import pytest

from conftest import NonLoopbackBlocked

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "darkroom_ai"

# The only module allowed to import network libraries.
NETWORK_ALLOWLIST = {"net/loopback.py"}

# Import roots that can open connections, plus cloud SDKs and telemetry we never want.
FORBIDDEN_ROOTS = {
    "socket",
    "socketserver",
    "ssl",
    "http",
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
    "urllib3",
    "requests",
    "aiohttp",
    "websockets",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "xmlrpc",
    "uvicorn",
    "starlette",
    "pooch",
    "openai",
    "anthropic",
    "boto3",
    "botocore",
    "google",
    "azure",
    "sentry_sdk",
    "opentelemetry",
}
# urllib.parse only handles strings; the rest of urllib can open connections.
FORBIDDEN_PREFIXES = ("urllib.request", "urllib.error", "urllib.response", "urllib.robotparser")
PUBLIC_IP = "93.184.216.34"


def _source_files() -> list[Path]:
    files = sorted(SRC.rglob("*.py"))
    assert files, f"no source files found under {SRC}"
    return files


def _is_forbidden(module: str) -> bool:
    if module == "urllib":
        return True
    return module.split(".")[0] in FORBIDDEN_ROOTS or module.startswith(FORBIDDEN_PREFIXES)


def _imports(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "urllib":
                found += [(node.lineno, f"urllib.{alias.name}") for alias in node.names]
            else:
                found.append((node.lineno, node.module))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
            in {"__import__", "import_module"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.append((node.lineno, node.args[0].value))
    return found


def test_guard_blocks_non_loopback_connect() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(NonLoopbackBlocked),
    ):
        sock.connect((PUBLIC_IP, 80))


def test_guard_blocks_connect_ex_and_udp() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(NonLoopbackBlocked),
    ):
        sock.connect_ex((PUBLIC_IP, 80))
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
        pytest.raises(NonLoopbackBlocked),
    ):
        sock.sendto(b"x", (PUBLIC_IP, 53))


def test_guard_blocks_name_lookup_and_create_connection() -> None:
    with pytest.raises(NonLoopbackBlocked):
        socket.getaddrinfo("example.com", 80)
    with pytest.raises(NonLoopbackBlocked):
        socket.create_connection(("example.com", 80), timeout=1)


def test_guard_blocks_asyncio_connections() -> None:
    async def attempt() -> None:
        await asyncio.open_connection(PUBLIC_IP, 80)

    with pytest.raises(NonLoopbackBlocked):
        asyncio.run(attempt())


def test_guard_allows_loopback() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            conn, _ = server.accept()
            with conn:
                client.sendall(b"ping")
                assert conn.recv(4) == b"ping"


def test_scanner_recognises_forbidden_imports() -> None:
    code = (
        "import socket\nimport os\nfrom urllib import request\nfrom urllib.parse import urlsplit\n"
        "import http.client\nfrom httpx2 import Client\nimport importlib\n"
        "importlib.import_module('requests')\n"
    )
    flagged = sorted(name for _, name in _imports(ast.parse(code)) if _is_forbidden(name))
    assert flagged == ["http.client", "httpx2", "requests", "socket", "urllib.request"]


def test_no_network_imports_outside_loopback_module() -> None:
    offenders = []
    for path in _source_files():
        relative = path.relative_to(SRC).as_posix()
        if relative in NETWORK_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [
            f"{relative}:{line} imports {name}"
            for line, name in _imports(tree)
            if _is_forbidden(name)
        ]
    assert not offenders, "network imports are only allowed in net/loopback.py:\n" + "\n".join(
        offenders
    )


def test_no_model_names_or_wildcard_binds_in_source() -> None:
    # The model names live only in the parked example file; none may appear in the source.
    with (REPO / "legacy" / "llm_agent" / "darkroom.llm.example.toml").open("rb") as handle:
        profiles = tomllib.load(handle)["llm"]["profiles"]
    banned = {profile["model"] for profile in profiles.values()} | {"0.0.0.0"}
    assert len(banned) > 1
    offenders = [
        f"{path.relative_to(SRC).as_posix()} contains {text!r}"
        for path in _source_files()
        for text in banned
        if text in path.read_text(encoding="utf-8")
    ]
    assert not offenders, "\n".join(offenders)
