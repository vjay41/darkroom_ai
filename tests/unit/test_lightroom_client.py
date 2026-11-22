from __future__ import annotations

import re
import time
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath

import pytest

from darkroom_ai.adapters.lightroom import plugin_dir
from darkroom_ai.adapters.lightroom.client import LightroomClient
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.interfaces import LightroomAdapter
from fake_lightroom import FakeLightroomPlugin


def _client(plugin: FakeLightroomPlugin, *, token: str | None = None, timeout_s: float = 5.0):
    return LightroomClient(
        host="127.0.0.1",
        request_port=plugin.request_port,
        response_port=plugin.response_port,
        token=token or plugin.token,
        timeout_s=timeout_s,
        export_timeout_s=timeout_s,
        connect_timeout_s=timeout_s,
        reconnect_delay_s=0.05,
    )


@pytest.fixture
def plugin() -> Iterator[FakeLightroomPlugin]:
    with FakeLightroomPlugin() as fake:
        yield fake


@pytest.fixture
def client(plugin: FakeLightroomPlugin) -> Iterator[LightroomClient]:
    with _client(plugin) as lightroom:
        yield lightroom


def test_client_satisfies_the_adapter_protocol(client: LightroomClient) -> None:
    adapter: LightroomAdapter = client
    assert adapter.ping().writes_allowed is True


def test_ping(client: LightroomClient) -> None:
    info = client.ping()
    assert info.lightroom_version == "15.2.1"
    assert info.catalog_path == info.configured_catalog


def test_selftest_round_trips_awkward_values(client: LightroomClient) -> None:
    value = {
        "path": "D:\\photos\\new\\tab\there.jpg",
        "quote": 'say "hi"',
        "unicode": "caf\u00e9 \u65e5\u672c \U0001f4f7",
        "numbers": [0, -1, 1.5, 1e-3],
        "nested": {"list": [[1, 2], {"a": None}], "flag": False},
    }
    assert client.selftest(value) == value


def test_full_edit_flow_addresses_photos_by_id(
    plugin: FakeLightroomPlugin, client: LightroomClient
) -> None:
    selection = client.get_selection()
    assert selection.active is not None
    original = selection.active
    assert original.file_format == "JPG"

    copy = client.create_virtual_copy(original.id, "darkroom")
    assert copy.is_virtual_copy
    assert copy.master_id == original.id
    assert copy.id != original.id

    snapshot = client.create_snapshot(copy.id, "darkroom-before")
    assert snapshot.created
    assert snapshot.snapshot is not None

    change = client.apply_develop_settings(copy.id, {"Exposure": 0.3})
    assert change.before == {"Exposure": 0.0}
    assert change.after == {"Exposure": 0.3}
    assert client.get_develop_settings(copy.id, ["Exposure"]) == {"Exposure": 0.3}
    # The original photo is untouched.
    assert client.get_develop_settings(original.id, ["Exposure"]) == {"Exposure": 0.0}

    preview = client.export_preview(copy.id, "preview-1", long_edge=512)
    assert preview.path == Path("D:\\previews\\preview-1.jpg")
    assert plugin.exports[-1]["long_edge"] == 512

    applied = client.apply_snapshot(copy.id, snapshot.snapshot.id)
    assert applied.name == "darkroom-before"
    assert client.get_develop_settings(copy.id, ["Exposure"]) == {"Exposure": 0.0}
    assert [s.name for s in client.list_snapshots(copy.id)] == ["darkroom-before"]
    assert client.get_photo(copy.id).snapshots[0].name == "darkroom-before"


def test_list_commands_matches_the_plugin_source(client: LightroomClient) -> None:
    commands = client.list_commands()
    assert "export_tiff" in commands.write
    assert "list_commands" in commands.read
    assert not set(commands.read) & set(commands.write)
    # The fake and the Lua plug-in register the same command names.
    code = (plugin_dir() / "Commands.lua").read_text(encoding="utf-8")
    assert set(commands.write) == set(re.findall(r"function Commands\.write\.(\w+)", code))
    lua_reads = set(re.findall(r"function Commands\.read\.(\w+)", code))
    assert set(commands.read) == lua_reads - {"probe"}


def test_find_photo_by_path(client: LightroomClient) -> None:
    found = client.find_photo("D:\\photos\\DSC_0001.jpg")
    assert found is not None
    assert found.id == "uuid-1"
    assert client.find_photo("D:\\photos\\missing.jpg") is None


def test_develop_edits_are_refused_on_master_photos(
    plugin: FakeLightroomPlugin, client: LightroomClient
) -> None:
    with pytest.raises(DarkroomError) as caught:
        client.apply_develop_settings("uuid-1", {"Exposure2012": 0.3})
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert caught.value.details["plugin_code"] == "NOT_A_VIRTUAL_COPY"
    assert plugin.photos["uuid-1"].settings["Exposure"] == 0.0


def test_writes_are_refused_outside_the_photos_root() -> None:
    with (
        FakeLightroomPlugin(photos_root=r"D:\darkroom_test") as fake,
        _client(fake) as lightroom,
    ):
        assert lightroom.get_photo("uuid-1").path == r"D:\photos\DSC_0001.jpg"
        for write in (
            lambda: lightroom.create_virtual_copy("uuid-1", "x"),
            lambda: lightroom.create_snapshot("uuid-1", "x"),
            lambda: lightroom.export_preview("uuid-1", "x"),
        ):
            with pytest.raises(DarkroomError) as caught:
                write()
            assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
            assert caught.value.details["plugin_code"] == "PHOTO_OUTSIDE_ROOT"
        assert len(fake.photos) == 1


def test_writes_are_allowed_inside_the_photos_root() -> None:
    with FakeLightroomPlugin(photos_root=r"D:\photos") as fake, _client(fake) as lightroom:
        copy = lightroom.create_virtual_copy("uuid-1", "x")
        assert lightroom.apply_develop_settings(copy.id, {"Exposure": 0.2}).after == {
            "Exposure": 0.2
        }


def test_export_tiff_only_for_virtual_copies_and_never_overwrites(client: LightroomClient) -> None:
    with pytest.raises(DarkroomError) as caught:
        client.export_tiff("uuid-1", "handoff-1")
    assert caught.value.details["plugin_code"] == "NOT_A_VIRTUAL_COPY"

    copy = client.create_virtual_copy("uuid-1", "x")
    exported = client.export_tiff(copy.id, "handoff-1")
    assert exported.bit_depth == 16
    # The plug-in answers with a Windows path; read it as one on any OS.
    assert PureWindowsPath(str(exported.path)).name == "handoff-1.tif"

    with pytest.raises(DarkroomError) as caught:
        client.export_tiff(copy.id, "handoff-1")
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED
    assert caught.value.details["plugin_code"] == "FILE_EXISTS"


def test_export_tiff_is_refused_on_the_wrong_catalog() -> None:
    with (
        FakeLightroomPlugin(configured_catalog="D:\\other\\Main.lrcat") as fake,
        _client(fake) as lightroom,
        pytest.raises(DarkroomError) as caught,
    ):
        lightroom.export_tiff("uuid-1", "handoff-1")
    assert caught.value.code is ErrorCode.LR_WRONG_CATALOG


def test_second_snapshot_with_same_name_is_not_created(client: LightroomClient) -> None:
    assert client.create_snapshot("uuid-1", "s").created is True
    assert client.create_snapshot("uuid-1", "s").created is False


def test_wrong_token_is_rejected(plugin: FakeLightroomPlugin) -> None:
    with _client(plugin, token="x" * 64) as lightroom, pytest.raises(DarkroomError) as caught:
        lightroom.ping()
    assert caught.value.code is ErrorCode.LR_COMMAND_FAILED
    assert caught.value.details["plugin_code"] == "UNAUTHORIZED"
    assert "setup" in caught.value.next_step


def test_writes_refused_on_wrong_catalog_but_reads_work() -> None:
    with (
        FakeLightroomPlugin(configured_catalog="D:\\other\\Main.lrcat") as fake,
        _client(fake) as lightroom,
    ):
        info = lightroom.ping()
        assert info.writes_allowed is False
        assert lightroom.get_selection().active is not None
        for write in (
            lambda: lightroom.apply_develop_settings("uuid-1", {"Exposure": 1.0}),
            lambda: lightroom.create_virtual_copy("uuid-1", "x"),
            lambda: lightroom.create_snapshot("uuid-1", "x"),
            lambda: lightroom.export_preview("uuid-1", "x"),
        ):
            with pytest.raises(DarkroomError) as caught:
                write()
            assert caught.value.code is ErrorCode.LR_WRONG_CATALOG
        assert fake.photos["uuid-1"].settings["Exposure"] == 0.0
        assert len(fake.photos) == 1


def test_unknown_photo(client: LightroomClient) -> None:
    with pytest.raises(DarkroomError) as caught:
        client.get_photo("nope")
    assert caught.value.code is ErrorCode.LR_COMMAND_FAILED
    assert caught.value.details["plugin_code"] == "PHOTO_NOT_FOUND"


def test_silent_plugin_times_out_with_lr_not_reachable() -> None:
    with FakeLightroomPlugin(silent=True) as fake, _client(fake, timeout_s=0.5) as lightroom:
        started = time.monotonic()
        with pytest.raises(DarkroomError) as caught:
            lightroom.ping()
        elapsed = time.monotonic() - started
    assert caught.value.code is ErrorCode.LR_NOT_REACHABLE
    assert 0.4 <= elapsed < 3
    assert "dialog" in caught.value.next_step


def test_nothing_listening_is_lr_not_reachable() -> None:
    with FakeLightroomPlugin() as fake:
        ports = (fake.request_port, fake.response_port)
    lightroom = LightroomClient(
        host="127.0.0.1",
        request_port=ports[0],
        response_port=ports[1],
        token="t" * 64,
        timeout_s=1,
        connect_timeout_s=1,
    )
    with pytest.raises(DarkroomError) as caught:
        lightroom.ping()
    assert caught.value.code is ErrorCode.LR_NOT_REACHABLE


def test_non_local_host_is_refused() -> None:
    lightroom = LightroomClient(
        host="192.168.1.50", request_port=58763, response_port=58764, token="t" * 64, timeout_s=1
    )
    with pytest.raises(DarkroomError) as caught:
        lightroom.ping()
    assert caught.value.code is ErrorCode.NON_LOCAL_ENDPOINT


def test_garbage_response_is_reported() -> None:
    with (
        FakeLightroomPlugin(garbage=True) as fake,
        _client(fake) as lightroom,
        pytest.raises(DarkroomError) as caught,
    ):
        lightroom.ping()
    assert caught.value.code is ErrorCode.LR_COMMAND_FAILED


def test_a_second_client_right_after_the_first_connects(plugin: FakeLightroomPlugin) -> None:
    # Two client objects in the same process, back to back (this failed against real Lightroom
    # before the disconnect time was shared between client objects).
    for _ in range(3):
        with _client(plugin) as lightroom:
            assert lightroom.ping().protocol == 1


def test_command_deadline_starts_after_connecting() -> None:
    with FakeLightroomPlugin(silent=True) as fake:
        lightroom = LightroomClient(
            host="127.0.0.1",
            request_port=fake.request_port,
            response_port=fake.response_port,
            token=fake.token,
            timeout_s=30,
            connect_timeout_s=0.6,
            reconnect_delay_s=0.05,
        )
        started = time.monotonic()
        with pytest.raises(DarkroomError) as caught:
            lightroom.ping()
        assert caught.value.code is ErrorCode.LR_NOT_REACHABLE
        assert time.monotonic() - started < 5


def test_client_reconnects_after_close(plugin: FakeLightroomPlugin) -> None:
    lightroom = _client(plugin)
    assert lightroom.ping().protocol == 1
    lightroom.close()
    assert lightroom.ping().protocol == 1
    lightroom.close()


def test_get_preset_settings_reads_without_applying(client: LightroomClient) -> None:
    presets = client.list_presets()
    settings = client.get_preset_settings(presets[0].uuid)
    assert settings == {"Exposure": 0.4, "Contrast": 10}
    with pytest.raises(DarkroomError) as caught:
        client.get_preset_settings("no-such-preset")
    assert caught.value.code is ErrorCode.LR_COMMAND_FAILED
    assert "get_preset" in client.list_commands().read


def test_catalog_listing_and_metadata_round_trip(
    plugin: FakeLightroomPlugin, client: LightroomClient
) -> None:
    selection = client.get_selection()
    assert selection.active is not None
    page = client.list_catalog(0, 10)
    assert page.total >= 1 and page.returned == len(page.photos)
    assert all(not photo.is_virtual_copy for photo in page.photos)
    meta = client.get_metadata(selection.active.id)
    assert meta.id == selection.active.id and meta.camera_model == "NIKON D7500"
    assert client.list_catalog(0, 10, path_contains="no-such-folder-xyz").total == 0


def test_every_request_carries_the_time_the_client_stops_waiting(
    plugin: FakeLightroomPlugin, client: LightroomClient
) -> None:
    before = time.time()
    client.ping()
    sent = [d for d in plugin.deadlines if d is not None]
    assert sent and all(before < d < before + 600 for d in sent)


@pytest.mark.parametrize("code", ["BRIDGE_RESTARTING", "BRIDGE_BUSY", "REQUEST_EXPIRED"])
def test_plugin_restart_busy_and_expiry_replies_say_nothing_was_executed(code: str) -> None:
    error = LightroomClient._error("set_mask_values", {"code": code, "message": "x"})
    assert error.code is ErrorCode.LR_NOT_REACHABLE
    assert "Nothing was executed" in error.next_step
    assert error.details["plugin_code"] == code


def test_bridge_status_is_available_and_timeout_replies_say_to_check_state(
    client: LightroomClient,
) -> None:
    assert client.bridge_status().instance == "fake-1"
    error = LightroomClient._error("create_mask", {"code": "COMMAND_TIMEOUT", "message": "slow"})
    assert error.code is ErrorCode.LR_COMMAND_FAILED and "state" in error.next_step
    busy = LightroomClient._error(
        "ping", {"code": "BRIDGE_BUSY", "message": "busy", "retry_after_s": 20}
    )
    assert "about 20 s" in busy.next_step and busy.details["retry_after_s"] == 20


def test_photo_not_shown_error_mentions_collapsed_stacks() -> None:
    error = LightroomClient._error(
        "inspect_masks", {"code": "MASK_PHOTO_NOT_SHOWN", "message": "showing another photo"}
    )
    assert error.code is ErrorCode.LR_BUSY_USER
    assert "Expand All Stacks" in error.next_step and "Do not click" in error.next_step
