"""Checks on the Lua plug-in and bridge.json that do not need Lightroom."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from darkroom_ai.adapters.lightroom import plugin_dir
from darkroom_ai.adapters.lightroom.bridge_file import (
    bridge_path,
    load_bridge_file,
    write_bridge_file,
)
from darkroom_ai.config import parse_settings
from darkroom_ai.errors import DarkroomError, ErrorCode

LUA_FILES = sorted(plugin_dir().glob("*.lua"))

# Lightroom SDK namespaces the plug-in must never use: network, processes, credentials.
FORBIDDEN_LUA = [
    "LrHttp",
    "LrFtp",
    "LrShell",
    "LrPasswords",
    "LrTasks.execute",
    "os.execute",
    "io.popen",
    "loadstring",
    "dofile",
    "loadfile",
]


def _settings(tmp_path: Path, catalog: str | None = "D:/cat/Test.lrcat"):
    data = {
        "llm": {
            "active_profile": "m",
            "profiles": {"m": {"base_url": "http://127.0.0.1:1234/v1", "model": "x"}},
        },
        "paths": {"previews_dir": str(tmp_path / "previews")},
    }
    if catalog:
        data["lightroom"] = {"test_catalog": catalog}
    return parse_settings(data)


def test_plugin_files_exist() -> None:
    names = {path.name for path in LUA_FILES}
    assert {"Info.lua", "Init.lua", "Bridge.lua", "Commands.lua", "Json.lua"} <= names
    info = (plugin_dir() / "Info.lua").read_text(encoding="utf-8")
    for name in re.findall(r'=\s*"([A-Za-z]+\.lua)"', info):
        assert (plugin_dir() / name).is_file(), f"Info.lua refers to missing {name}"


@pytest.mark.parametrize("path", LUA_FILES, ids=lambda p: p.name)
def test_plugin_uses_no_network_or_process_apis(path: Path) -> None:
    code = "\n".join(
        line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines()
    )
    found = [name for name in FORBIDDEN_LUA if name in code]
    assert not found, f"{path.name} uses {found}"


def test_only_bridge_binds_sockets_and_never_logs_the_token() -> None:
    for path in LUA_FILES:
        code = path.read_text(encoding="utf-8")
        if path.name != "Bridge.lua":
            assert not re.search(r"import\s*\(?\s*[\"']LrSocket", code)
        for line in code.splitlines():
            if "logger:" in line:
                assert "token" not in line.replace("wrong or missing token", "")
                assert "params" not in line


def test_write_commands_match_between_plugin_and_fake() -> None:
    from fake_lightroom import WRITE_COMMANDS

    code = (plugin_dir() / "Commands.lua").read_text(encoding="utf-8")
    assert set(re.findall(r"function Commands\.write\.(\w+)", code)) == WRITE_COMMANDS
    reads = set(re.findall(r"function Commands\.read\.(\w+)", code))
    assert {"ping", "get_selection", "get_photo", "list_snapshots"} <= reads


@pytest.mark.parametrize("path", LUA_FILES, ids=lambda p: p.name)
def test_lua_syntax(path: Path) -> None:
    luac = os.environ.get("DARKROOM_LUAC")
    if not luac:
        pytest.skip("set DARKROOM_LUAC to the SDK's luac.exe to syntax-check the plug-in")
    result = subprocess.run([luac, "-p", str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_write_and_load_bridge_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    bridge = write_bridge_file(settings, folder=tmp_path)
    assert len(bridge.token) == 64
    assert bridge.catalog_path == str(Path("D:/cat/Test.lrcat"))
    assert Path(bridge.preview_dir).is_absolute()
    assert (bridge.request_port, bridge.response_port) == (58763, 58764)
    assert load_bridge_file(tmp_path) == bridge
    assert set(json.loads(bridge_path(tmp_path).read_text(encoding="utf-8"))) == {
        "token",
        "catalog_path",
        "preview_dir",
        "request_port",
        "response_port",
        "photos_root",
    }


def test_token_is_kept_unless_rotated(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = write_bridge_file(settings, folder=tmp_path)
    assert write_bridge_file(settings, folder=tmp_path).token == first.token
    assert write_bridge_file(settings, folder=tmp_path, rotate_token=True).token != first.token


def test_setup_needs_a_configured_catalog(tmp_path: Path) -> None:
    with pytest.raises(DarkroomError) as caught:
        write_bridge_file(_settings(tmp_path, catalog=None), folder=tmp_path)
    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert not bridge_path(tmp_path).exists()


def test_missing_or_broken_bridge_file(tmp_path: Path) -> None:
    with pytest.raises(DarkroomError) as caught:
        load_bridge_file(tmp_path)
    assert caught.value.code is ErrorCode.CONFIG_INVALID
    bridge_path(tmp_path).write_text('{"token": "short"}', encoding="utf-8")
    with pytest.raises(DarkroomError) as caught:
        load_bridge_file(tmp_path)
    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_bridge_file_is_git_ignored() -> None:
    repo = Path(__file__).resolve().parents[2]
    assert "bridge.json" in (repo / ".gitignore").read_text(encoding="utf-8").split()
