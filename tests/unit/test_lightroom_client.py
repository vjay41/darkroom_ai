from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

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


def test_find_photo_by_path(client: LightroomClient) -> None:
    found = client.find_photo("D:\\photos\\DSC_0001.jpg")
    assert found is not None
    assert found.id == "uuid-1"
    assert client.find_photo("D:\\photos\\missing.jpg") is None


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
        host="127.0.0.1", request_port=ports[0], response_port=ports[1], token="t" * 64, timeout_s=1
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


def test_client_reconnects_after_close(plugin: FakeLightroomPlugin) -> None:
    lightroom = _client(plugin)
    assert lightroom.ping().protocol == 1
    lightroom.close()
    assert lightroom.ping().protocol == 1
    lightroom.close()
