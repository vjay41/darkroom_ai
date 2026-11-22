"""Catalog management: finding catalogs, the owner's allowlist, collections and keywords."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_tools import Rig

from darkroom_ai.adapters.lightroom.bridge_file import (
    BridgeAccessStore,
    bridge_path,
    load_bridge_file,
    write_bridge_file,
)
from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools.catalog_management import find_catalogs
from fakes import AllowAll, FakeAccess


def rig_with_access(tmp_path: Path, *, allow: bool = False) -> Rig:
    rig = Rig(tmp_path, confirmer=AllowAll() if allow else None)
    rig.context.access = FakeAccess(["D:\\cat\\Test.lrcat"], ["D:\\darkroom_test"])
    return rig


def make_catalog(folder: Path, name: str = "x.lrcat") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"sqlite")
    return path


# -- list_catalogs --------------------------------------------------------------------------------


def test_find_catalogs_is_shallow_and_skips_backups(tmp_path: Path) -> None:
    near = make_catalog(tmp_path / "Lightroom", "A.lrcat")
    make_catalog(tmp_path / "Backups" / "2026", "backup.lrcat")
    make_catalog(tmp_path / "a" / "b" / "c" / "d", "too_deep.lrcat")
    (tmp_path / "Lightroom" / "A.lrcat-data").mkdir()
    assert find_catalogs([tmp_path]) == [near]


def test_list_catalogs_marks_the_open_and_the_allowed_ones(tmp_path: Path) -> None:
    rig = rig_with_access(tmp_path / "rig")
    folder = tmp_path / "cats"
    mine = make_catalog(folder, "mine.lrcat")
    rig.context.access = FakeAccess([str(mine)])
    got = rig.call("list_catalogs", folder=str(folder))
    assert got["open_catalog"] == "D:\\cat\\Test.lrcat"
    (entry,) = [c for c in got["catalogs"] if c["path"] == str(mine)]
    assert entry["changes_allowed"] is True and entry["is_open"] is False
    assert "File > Open Catalog" in got["note"]


def test_list_catalogs_rejects_a_folder_that_does_not_exist(tmp_path: Path) -> None:
    rig = rig_with_access(tmp_path)
    with pytest.raises(DarkroomError):
        rig.call("list_catalogs", folder=str(tmp_path / "missing"))


# -- the owner's allowlist ------------------------------------------------------------------------


def test_a_catalog_is_not_allowed_unless_the_owner_says_yes(tmp_path: Path) -> None:
    rig = rig_with_access(tmp_path / "rig")  # refuses every prompt
    real = make_catalog(tmp_path / "real", "Lightroom Catalog.lrcat")
    with pytest.raises(DarkroomError) as caught:
        rig.call("request_catalog_access", catalog_path=str(real))
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED
    assert rig.context.access is not None
    assert str(real) not in rig.context.access.allowed_catalogs()


def test_the_owner_prompt_shows_the_full_path_and_allows_it(tmp_path: Path) -> None:
    confirmer = AllowAll()
    rig = Rig(tmp_path / "rig", confirmer=confirmer)
    rig.context.access = FakeAccess(["D:\\cat\\Test.lrcat"])
    real = make_catalog(tmp_path / "real", "Lightroom Catalog.lrcat")
    photos = tmp_path / "photos"
    photos.mkdir()
    got = rig.call("request_catalog_access", catalog_path=str(real), photo_folders=[str(photos)])
    assert got["changed"] is True
    assert str(real) in confirmer.asked[0]
    assert str(real) in got["allowed_catalogs"] and str(photos) in got["allowed_photo_folders"]
    again = rig.call("request_catalog_access", catalog_path=str(real))
    assert again["changed"] is False and len(confirmer.asked) == 1


def test_only_an_existing_lrcat_file_can_be_requested(tmp_path: Path) -> None:
    rig = rig_with_access(tmp_path / "rig", allow=True)
    (tmp_path / "notes.txt").write_text("x")
    for bad in (str(tmp_path / "notes.txt"), str(tmp_path / "nope.lrcat"), str(tmp_path)):
        with pytest.raises(DarkroomError):
            rig.call("request_catalog_access", catalog_path=bad)


def test_a_photo_folder_needs_a_yes_and_must_exist(tmp_path: Path) -> None:
    denied = rig_with_access(tmp_path / "a")
    folder = tmp_path / "new_photos"
    folder.mkdir()
    with pytest.raises(DarkroomError) as caught:
        denied.call("request_folder_access", folder=str(folder))
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED

    allowed = rig_with_access(tmp_path / "b", allow=True)
    assert allowed.call("request_folder_access", folder=str(folder))["changed"] is True
    with pytest.raises(DarkroomError):
        allowed.call("request_folder_access", folder=str(tmp_path / "missing"))


def test_no_allowlist_means_a_clear_error(tmp_path: Path) -> None:
    rig = Rig(tmp_path, confirmer=AllowAll())
    with pytest.raises(DarkroomError) as caught:
        rig.call("request_folder_access", folder=str(tmp_path))
    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_the_bridge_file_keeps_the_owners_approvals(tmp_path: Path) -> None:
    from test_lightroom_plugin_files import _settings

    settings = _settings(tmp_path)
    write_bridge_file(settings, folder=tmp_path)
    store = BridgeAccessStore(tmp_path)
    store.allow_catalog("E:\\Photos\\Main.lrcat")
    store.allow_catalog("e:/photos/main.lrcat")  # the same catalog, spelled differently
    store.allow_photos_folder("E:\\Photos")
    assert store.allowed_catalogs() == (str(Path("D:/cat/Test.lrcat")), "E:\\Photos\\Main.lrcat")
    assert "E:\\Photos" in store.photos_roots()
    # running setup again must not drop what the owner approved, and the token stays
    token = load_bridge_file(tmp_path).token
    again = write_bridge_file(settings, folder=tmp_path)
    assert again.allowed_catalogs == ("E:\\Photos\\Main.lrcat",) and again.token == token
    raw = json.loads(bridge_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["photos_roots"] == ["E:\\Photos"]


# -- collections and keywords ---------------------------------------------------------------------


def photo_ids(rig: Rig, count: int) -> list[str]:
    ids = []
    for number in range(count):
        photo_id = f"extra-{number}"
        rig.lightroom.add_photo(photo_id, f"IMG_{number:04d}.jpg")
        ids.append(photo_id)
    return ids


def test_sets_collections_and_membership_are_read_back(tmp_path: Path) -> None:
    rig = rig_with_access(tmp_path)
    assert rig.call("create_collection_set", path="Trips")["created"] is True
    assert rig.call("create_collection_set", path="Trips")["created"] is False
    made = rig.call("create_collection", path="Trips/Japan")
    assert made["created"] is True and made["path"] == "Trips/Japan"
    ids = photo_ids(rig, 3)
    added = rig.call("add_to_collection", collection="Trips/Japan", photo_ids=ids)
    assert added["changed_ids"] == ids and added["photo_count"] == 3
    again = rig.call("add_to_collection", collection="Trips/Japan", photo_ids=ids[:2])
    assert again["changed_ids"] == [] and again["already_in"] == 2
    listed = rig.call("get_collection_photos", collection="Trips/Japan")
    assert [p["id"] for p in listed["photos"]] == ids and listed["total"] == 3
    info = rig.call("get_catalog_info")
    trips = info["catalog"]["collections"][0]
    assert trips["name"] == "Trips" and trips["children"][0]["photo_count"] == 3
    assert "File > Open Catalog" in info["note"]


def test_a_collection_needs_its_set_and_unknown_photos_are_reported(tmp_path: Path) -> None:
    rig = rig_with_access(tmp_path)
    with pytest.raises(DarkroomError):
        rig.call("create_collection", path="Missing/Child")
    rig.call("create_collection", path="Keepers")
    got = rig.call("add_to_collection", collection="Keepers", photo_ids=["master-1", "ghost"])
    assert got["changed_ids"] == ["master-1"] and got["missing_ids"] == ["ghost"]
    with pytest.raises(DarkroomError):
        rig.call("add_to_collection", collection="Nope", photo_ids=["master-1"])


def test_adding_more_than_ten_photos_needs_the_go_ahead(tmp_path: Path) -> None:
    refused = rig_with_access(tmp_path / "a")
    refused.call("create_collection", path="Big")
    ids = photo_ids(refused, 11)
    with pytest.raises(DarkroomError) as caught:
        refused.call("add_to_collection", collection="Big", photo_ids=ids)
    assert caught.value.code is ErrorCode.CONFIRMATION_REQUIRED
    assert refused.lightroom.collections["Big"] == []
    allowed = rig_with_access(tmp_path / "b", allow=True)
    allowed.call("create_collection", path="Big")
    ids = photo_ids(allowed, 11)
    assert (
        len(allowed.call("add_to_collection", collection="Big", photo_ids=ids)["changed_ids"]) == 11
    )


def test_collection_changes_can_be_taken_back_newest_first(tmp_path: Path) -> None:
    rig = rig_with_access(tmp_path)
    rig.call("create_collection_set", path="Trips")
    rig.call("create_collection", path="Trips/Japan")
    ids = photo_ids(rig, 2)
    rig.call("add_to_collection", collection="Trips/Japan", photo_ids=ids)
    rig.call("remove_from_collection", collection="Trips/Japan", photo_ids=ids[:1])
    assert rig.lightroom.collections["Trips/Japan"] == [ids[1]]
    assert rig.call("undo_collection_change")["undone_steps"] == 1  # the removal
    assert rig.lightroom.collections["Trips/Japan"] == [ids[1], ids[0]]
    assert rig.call("undo_collection_change", scope="all")["undone_steps"] == 3
    assert "Trips/Japan" not in rig.lightroom.collections
    assert "Trips" not in rig.lightroom.collection_sets
    assert rig.call("undo_collection_change")["undone_steps"] == 0


def test_a_collection_that_still_has_photos_is_not_deleted(tmp_path: Path) -> None:
    rig = rig_with_access(tmp_path)
    rig.call("create_collection", path="Keepers")
    rig.call("add_to_collection", collection="Keepers", photo_ids=["master-1"])
    with pytest.raises(DarkroomError):
        rig.lightroom.delete_collection("Keepers", "collection")


def test_keywords_can_be_created_as_a_hierarchy(tmp_path: Path) -> None:
    rig = rig_with_access(tmp_path)
    got = rig.call("create_keyword", path="Places/Japan/Kyoto")
    assert got["created_levels"] == ["Places", "Japan", "Kyoto"]
    assert rig.call("create_keyword", path="places/japan")["created_levels"] == []
