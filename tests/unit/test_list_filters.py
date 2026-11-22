"""list_catalog_photos filters (rating, flag, keyword): tool layer and plug-in protocol."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_lightroom_client import _client
from test_tools import Rig

from darkroom_ai.errors import DarkroomError, ErrorCode
from fake_lightroom import FakeLightroomPlugin


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    rig = Rig(tmp_path)
    for number in (2, 3):
        rig.lightroom.add_photo(f"master-{number}", f"TEST_000{number}.jpg")
    library = rig.lightroom.library
    library["master-1"].update(rating=5, pick_status=1, keywords={"Wedding", "Bride"})
    library["master-2"].update(rating=2, pick_status=-1, keywords={"wedding"})
    return rig  # master-3 is unrated, unflagged, untagged


def ids(rig: Rig, **filters: Any) -> list[str]:
    page = rig.call("list_catalog_photos", limit=100, **filters)["page"]
    return [photo["id"] for photo in page["photos"]]


def test_without_filters_everything_is_listed(rig: Rig) -> None:
    assert ids(rig) == ["master-1", "master-2", "master-3"]


def test_rating_range_and_the_unrated(rig: Rig) -> None:
    assert ids(rig, min_rating=3) == ["master-1"]
    assert ids(rig, min_rating=2, max_rating=4) == ["master-2"]
    assert ids(rig, max_rating=0) == ["master-3"]  # unrated counts as 0


def test_flag(rig: Rig) -> None:
    assert ids(rig, flag="pick") == ["master-1"]
    assert ids(rig, flag="reject") == ["master-2"]
    assert ids(rig, flag="none") == ["master-3"]


def test_keyword_matches_any_letter_case(rig: Rig) -> None:
    assert ids(rig, keyword="WEDDING") == ["master-1", "master-2"]
    assert ids(rig, keyword="bride") == ["master-1"]
    assert ids(rig, keyword="nothing-like-this") == []


def test_filters_combine_with_and_and_the_total_counts_matches_only(rig: Rig) -> None:
    assert ids(rig, keyword="wedding", min_rating=3) == ["master-1"]
    page = rig.call("list_catalog_photos", limit=1, keyword="wedding")["page"]
    assert (page["total"], page["returned"]) == (2, 1)
    assert page["filters_applied"] == ["keyword"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"min_rating": 4, "max_rating": 2},
        {"min_rating": 6},
        {"max_rating": -1},
        {"flag": "star"},
        {"keyword": ""},
        {"edited": True},  # there is no such filter
    ],
)
def test_bad_filters_are_rejected_before_listing(rig: Rig, arguments: dict[str, Any]) -> None:
    calls = len(rig.lightroom.calls)
    with pytest.raises(DarkroomError) as caught:
        rig.call("list_catalog_photos", **arguments)
    assert caught.value.code is ErrorCode.LLM_BAD_TOOL_CALL
    assert "list_catalog" not in rig.lightroom.calls[calls:]


# -- the plug-in protocol -------------------------------------------------------------------------


@pytest.fixture
def plugin() -> Iterator[FakeLightroomPlugin]:
    with FakeLightroomPlugin() as fake:
        yield fake


def test_filters_travel_to_the_plugin_and_are_confirmed(plugin: FakeLightroomPlugin) -> None:
    plugin.photos["uuid-1"].rating = 4
    with _client(plugin) as client:
        page = client.list_catalog(0, 10, min_rating=3, keyword=None, flag="none")
        assert page.filters_applied == ("min_rating", "flag")
        assert page.total == 1  # rated 4 and unflagged: both filters match
        assert client.list_catalog(0, 10, min_rating=5).total == 0
        assert client.list_catalog(0, 10, min_rating=4).total == 1


def test_an_older_plugin_that_ignores_filters_is_never_shown_as_filtered(
    plugin: FakeLightroomPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    def old_list_photos(params: dict[str, Any]) -> dict[str, Any]:
        photos = [plugin._metadata(p) for p in plugin.photos.values()]
        return {"total": len(photos), "offset": 0, "returned": len(photos), "photos": photos}

    monkeypatch.setattr(plugin, "_cmd_list_photos", old_list_photos)
    with _client(plugin) as client:
        assert client.list_catalog(0, 10).total == 1  # no filter asked: fine
        with pytest.raises(DarkroomError) as caught:
            client.list_catalog(0, 10, min_rating=5)
    assert caught.value.code is ErrorCode.LR_COMMAND_FAILED
    assert "0.11.0" in str(caught.value)
    assert caught.value.details["ignored_filters"] == ["min_rating"]
