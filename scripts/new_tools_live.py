"""Live run of the Lightroom-native tools added in plug-in 0.14/0.15, on virtual copies only.

Needs Lightroom open on the configured test catalog with the darkroom_ai bridge started. Every step
is run on its own: a failure is recorded and the run goes on. Results go to the screen and to
outputs/new_tools_live.json. Masters are never written; everything made is undone where possible.

Usage:  poetry run python scripts/new_tools_live.py [--ai] [--only STEP,STEP]
  --ai   also run Lightroom's AI Enhance and Reflection Removal (slow, GPU)
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from darkroom_ai.config import load_config
from darkroom_ai.tools.factory import build_tool_context
from darkroom_ai.tools.registry import ToolRegistry

RESULTS: dict[str, dict[str, Any]] = {}
STAMP = time.strftime("%H%M%S")
Step = Callable[[], dict[str, Any] | None]


class Approver:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def confirm(self, action: str, details: Mapping[str, Any]) -> bool:
        self.asked.append(action)
        print(f"    go-ahead asked: {action}")
        return True


def short(data: Any, n: int = 500) -> str:
    return json.dumps(data, default=str)[:n]


def step(name: str, fn: Step, only: set[str]) -> None:
    if only and name not in only:
        return
    print(f"== {name}", flush=True)
    started = time.time()
    try:
        info = fn() or {}
        ok = bool(info.pop("ok", True))
        status = "PASS" if ok else "FAIL"
        RESULTS[name] = {"status": status, "seconds": round(time.time() - started, 1), **info}
    except Exception as exc:
        RESULTS[name] = {
            "status": "ERROR",
            "seconds": round(time.time() - started, 1),
            "error": f"{type(exc).__name__}: {exc}"[:600],
            "trace": traceback.format_exc()[-800:],
        }
    shown = {k: v for k, v in RESULTS[name].items() if k not in ("trace", "status")}
    print(f"   -> {RESULTS[name]['status']} {short(shown, 900)}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--ai", action="store_true")
    ap.add_argument(
        "--delete-smart", default="", help="delete one darkroom-live-smart-* collection"
    )
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {s for s in args.only.split(",") if s}

    settings = load_config(args.config)
    root = settings.lightroom.test_photos_dir
    assert root is not None
    approver = Approver()
    ctx = build_tool_context(settings, confirmer=approver)
    tools = ToolRegistry(ctx)
    lr = ctx.lightroom

    def call(tool: str, /, **kw: Any) -> dict[str, Any]:
        return tools.call(tool, kw)

    def master(file_name: str) -> str:
        found = lr.find_photo(str(root / file_name))
        assert found is not None, f"{file_name} not in the catalog"
        return found.id

    if args.delete_smart:
        if not args.delete_smart.startswith("darkroom-live-smart-"):
            raise SystemExit(
                "Refusing: only darkroom-live-smart-* collections may be deleted here."
            )
        print("deleted:", lr.delete_collection(args.delete_smart, "collection"))
        return

    m1, m2 = master("DSC_2411-1.jpg"), master("DSC_2412-2.jpg")
    st: dict[str, Any] = {}

    def exposure(photo: str) -> Any:
        got = call("get_develop_parameters", photo_id=photo, names=["Exposure2012"])
        return got["values"].get("Exposure2012")

    def s_connect() -> dict[str, Any]:
        return {"ok": True, "status": short(call("get_app_status"), 300)}

    def s_ai_panel() -> dict[str, Any]:
        out = call("get_ai_panel_state", photo_id=m1)
        return {"ok": bool(out.get("state")), "state": out["state"]}

    def s_snapshot() -> dict[str, Any]:
        made = call("create_snapshot", photo_id=m1, name=f"live-{STAMP}")
        c = made["photo_id"]
        st["c1"] = c
        before = exposure(c)
        call("adjust_lighting", photo_id=c, exposure=0.4)
        mid = exposure(c)
        listed = call("list_snapshots", photo_id=c)
        restored = call("restore_snapshot", photo_id=c, name=f"live-{STAMP}")
        time.sleep(2)
        after = exposure(c)
        return {
            "ok": mid != before and after == before,
            "before": before,
            "mid": mid,
            "after": after,
            "snapshots": len(listed.get("snapshots", [])),
            "restore": short(restored, 200),
        }

    def s_auto_correct() -> dict[str, Any]:
        out = call("auto_correct", photo_ids=[m1, m2], tone=True, white_balance=True)
        st["auto_batch"] = out.get("batch_id")
        return {
            "ok": int(out.get("ok", 0)) >= 1 and not out.get("identical_across_photos"),
            "out": short(out, 1200),
        }

    def s_auto_undo() -> dict[str, Any]:
        return {"ok": True, "undone": short(call("undo_batch", batch_id=st["auto_batch"]), 300)}

    def s_reset() -> dict[str, Any]:
        c = st["c1"]
        call("adjust_lighting", photo_id=c, contrast=20)
        out = call("reset_develop", photo_id=c, scope="all")
        got = call("get_develop_parameters", photo_id=c, names=["Contrast2012", "Exposure2012"])
        return {"ok": True, "after": short(got, 200), "result": short(out, 300)}

    def s_metadata() -> dict[str, Any]:
        c = st["c1"]
        out = call(
            "set_photo_metadata", photo_id=c, title=f"darkroom live {STAMP}", color_label="blue"
        )
        call("set_photo_metadata", photo_id=c, title="", color_label="none")
        seen = out["change"]["after"]["title"] == f"darkroom live {STAMP}"
        return {
            "ok": seen and out["change"]["after"]["color_label"] == "blue",
            "change": short(out.get("change"), 300),
        }

    def s_rotate() -> dict[str, Any]:
        c = st["c1"]
        a = call("rotate_photo", photo_id=c, direction="right")
        b = call("rotate_photo", photo_id=c, direction="left")
        good = a["rotation"]["after"] != a["rotation"]["before"]
        return {"ok": good, "right": short(a["rotation"]), "left": short(b["rotation"])}

    def s_find_photos() -> dict[str, Any]:
        def n(criteria: str, op: str, value: Any) -> int:
            found = call(
                "find_photos", conditions=[{"criteria": criteria, "operation": op, "value": value}]
            )
            return int(found["found"]["count"])

        jpg = n("fileFormat", "==", "JPG")
        return {
            "ok": jpg > 0,
            "jpg": jpg,
            "rating>=0": n("rating", ">=", 0),
            "red": n("labelColor", "==", "red"),
        }

    def s_catalog_lists() -> dict[str, Any]:
        kw = call("list_keywords")
        md = call("list_catalog_presets", kind="metadata")
        fn = call("list_catalog_presets", kind="filename")
        st["md_presets"] = md["presets"]
        return {
            "ok": True,
            "keywords": len(kw["keywords"]["keywords"]),
            "metadata": md["presets"],
            "filename": fn["presets"],
        }

    def s_smart_collection() -> dict[str, Any]:
        cond = [{"criteria": "rating", "operation": ">=", "value": 3}]
        path = f"darkroom-live-smart-{STAMP}"
        out = call("create_smart_collection", path=path, conditions=cond)
        undo = call("undo_collection_change")
        return {
            "ok": bool(out["created"]) and undo["undone_steps"] == 1,
            "made": short(out, 200),
            "undo": short(undo, 200),
        }

    def s_smart_previews() -> dict[str, Any]:
        return {"ok": call("build_smart_previews", photo_ids=[m1])["requested"] == 1}

    def s_metadata_preset() -> dict[str, Any]:
        names = st.get("md_presets") or []
        if not names:
            return {"ok": True, "skipped": "no metadata presets in this catalog"}
        out = call("apply_metadata_preset", photo_ids=[st["c1"]], preset=names[0])
        return {"ok": out["applied"] == 1, "preset": names[0]}

    def s_copy_settings() -> dict[str, Any]:
        src = st["c1"]
        call("adjust_lighting", photo_id=src, exposure=0.3, contrast=10)
        c2 = call("create_snapshot", photo_id=m2, name=f"live2-{STAMP}")["photo_id"]
        st["c2"] = c2
        out = call(
            "copy_develop_settings", source_photo_id=src, target_photo_ids=[c2], groups=["tone"]
        )
        got = call("get_develop_parameters", photo_id=c2, names=["Exposure2012", "Contrast2012"])
        return {"ok": True, "copied": short(out, 300), "target": short(got, 200)}

    def s_people() -> dict[str, Any]:
        out = call("remove_distracting_people", photo_id=st["c1"])
        return {
            "ok": bool(out["result"]["snapshot"]),
            "spots_added": out.get("spots_added"),
            "result": short(out["result"], 400),
        }

    def s_restore_after_people() -> dict[str, Any]:
        c = st["c1"]
        newest = call("list_snapshots", photo_id=c)["snapshots"][-1]
        out = call("restore_snapshot", photo_id=c, snapshot_id=newest.get("id"))
        return {
            "ok": True,
            "retouch": short(call("get_retouch_state", photo_id=c), 300),
            "restore": short(out, 200),
        }

    def s_crop_tilt() -> dict[str, Any]:
        c = st["c1"]
        tilt = call("measure_tilt", photo_id=c)
        plan = call("plan_crop", photo_id=c, aspect="4:5", angle=2.0)
        applied = call("crop", photo_id=c, **plan["crop_arguments"])
        after = call("measure_tilt", photo_id=c)
        return {
            "ok": True,
            "tilt_before": short(tilt, 300),
            "plan": short(plan, 300),
            "applied": short(applied, 300),
            "tilt_after": short(after, 300),
        }

    def s_crop_sign() -> dict[str, Any]:
        rank = {"low": 1, "medium": 2, "high": 3}

        def level(photo: str) -> tuple[tuple[int, float], dict[str, Any]]:
            h = call("measure_tilt", photo_id=photo)["reading"]["horizontal"]
            return (rank.get(str(h.get("confidence")), 0), float(h.get("length_share") or 0)), h

        names = ["DSC_2411-1.jpg", "DSC_2413-3.jpg", "DSC_2416-4.jpg", "DSC_2419-5.jpg"]
        scored = [(level(master(n)), n) for n in names]
        (conf, before), best = max(scored, key=lambda x: x[0][0])
        pid = master(best)
        plan = call("plan_crop", photo_id=pid, aspect="original", angle=3.0)
        call("crop", photo_id=pid, **plan["crop_arguments"])
        _, after = level(pid)
        call("reset_develop", photo_id=pid, scope="crop")
        want = round(float(before.get("tilt_deg", 0)), 2)
        plan2 = call("plan_crop", photo_id=pid, aspect="original", angle=want)
        call("crop", photo_id=pid, **plan2["crop_arguments"])
        _, levelled = level(pid)
        call("reset_develop", photo_id=pid, scope="crop")
        delta = float(after.get("tilt_deg", 0)) - float(before.get("tilt_deg", 0))
        return {
            "ok": abs(delta) > 1.0
            and abs(float(levelled.get("tilt_deg", 99))) < abs(float(before.get("tilt_deg", 0))),
            "angle_equal_to_measured_tilt": want,
            "tilt_after_levelling": levelled.get("tilt_deg"),
            "photo": best,
            "confidence": list(conf),
            "before": before.get("tilt_deg"),
            "after": after.get("tilt_deg"),
            "delta_for_angle_plus_3": round(delta, 2),
            "meaning": "negative delta: a positive angle turns the picture counter-clockwise",
        }

    def s_remove_objects() -> dict[str, Any]:
        region = [{"name": "test patch", "box": [0.60, 0.05, 0.70, 0.15], "shape": "ellipse"}]
        ps = call("remove_objects", photo_id=m1, regions=region, method="content_aware")
        small = [{"name": "tiny", "box": [0.60, 0.05, 0.63, 0.08], "shape": "ellipse"}]
        ip = call("remove_objects", photo_id=m1, regions=small, method="inpaint")
        paths = [ps.get("psd_path"), ps.get("tiff_path"), ps.get("jpg_path"), ip.get("jpg_path")]
        exists = [bool(x) and Path(x).exists() for x in paths if x]
        return {
            "ok": all(exists) and bool(ps.get("psd_path")),
            "method_used": ps["method"],
            "paths": paths,
            "exist": exists,
            "seconds_ps": ps["seconds"],
        }

    def s_red_eye() -> dict[str, Any]:
        out = call("fix_red_eye", photo_id=m1, eyes=[{"box": [0.40, 0.30, 0.45, 0.35]}])
        files = [out.get("jpg_path"), out.get("tiff_path")]
        exist = [bool(x) and Path(x).exists() for x in files if x]
        return {"ok": all(exist) and bool(exist), "files": files, "result": short(out, 500)}

    def s_retouch_state() -> dict[str, Any]:
        out = call("get_retouch_state", photo_id=m1)
        return {"ok": "state" in out, "state": short(out["state"], 300)}

    def s_cleanup() -> dict[str, Any]:
        """Put the test copies back to an unedited state (masters are never touched)."""
        done = {}
        for label, photo in (("DSC_2411-1", m1), ("DSC_2412-2", m2)):
            call("reset_develop", photo_id=photo, scope="all")
            got = call(
                "get_develop_parameters", photo_id=photo, names=["Exposure2012", "Contrast2012"]
            )
            done[label] = got["values"]
        clean = all(
            v.get("Exposure2012") in (0, None) and v.get("Contrast2012") in (0, None)
            for v in done.values()
        )
        return {"ok": clean, "values": done}

    def s_scene() -> dict[str, Any]:
        c = st["c1"]
        return {
            "ok": True,
            "zones": short(call("zone_map", photo_id=c), 400),
            "candidates": short(call("find_candidates", photo_id=c), 500),
        }

    def s_presets() -> dict[str, Any]:
        lst = call("list_presets", limit=3)
        names = [p["name"] for p in lst.get("presets", [])][:2]
        out = call("try_presets", photo_id=st["c1"], preset_names=names)
        return {"ok": bool(names), "names": names, "result": short(out, 600)}

    def s_export() -> dict[str, Any]:
        out = call(
            "export_for_delivery",
            photo_id=st["c1"],
            name=f"live-{STAMP}",
            long_edge=2048,
            quality=0.9,
        )
        return {"ok": True, "result": short(out, 400)}

    def s_enhance() -> dict[str, Any]:
        out = call("enhance", photo_id=master("DSC00482.ARW"), feature="denoise", amount=30)
        return {"ok": bool(out["run"]["callback_called"]), "run": short(out["run"], 900)}

    def s_reflection() -> dict[str, Any]:
        out = call("reflection_removal", photo_id=m2, quality="preview")
        return {"ok": bool(out["run"]["callback_called"]), "run": short(out["run"], 900)}

    plan: list[tuple[str, Step]] = [
        ("connect", s_connect),
        ("ai_panel", s_ai_panel),
        ("snapshot_restore", s_snapshot),
        ("auto_correct", s_auto_correct),
        ("auto_undo", s_auto_undo),
        ("reset", s_reset),
        ("metadata", s_metadata),
        ("rotate", s_rotate),
        ("find_photos", s_find_photos),
        ("catalog_lists", s_catalog_lists),
        ("smart_collection", s_smart_collection),
        ("smart_previews", s_smart_previews),
        ("metadata_preset", s_metadata_preset),
        ("copy_settings", s_copy_settings),
        ("people", s_people),
        ("restore_after_people", s_restore_after_people),
        ("crop_tilt", s_crop_tilt),
        ("crop_sign", s_crop_sign),
        ("remove_objects", s_remove_objects),
        ("red_eye", s_red_eye),
        ("retouch_state", s_retouch_state),
        ("scene", s_scene),
        ("presets", s_presets),
        ("export", s_export),
    ]
    if args.ai:
        plan += [("enhance", s_enhance), ("reflection", s_reflection)]
    if only and "cleanup" in only:
        plan.append(("cleanup", s_cleanup))
    for name, fn in plan:
        step(name, fn, only)

    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "new_tools_live.json").write_text(
        json.dumps(RESULTS, indent=2, default=str), encoding="utf-8"
    )
    print("\nSUMMARY")
    for name, r in RESULTS.items():
        print(f"  {r['status']:5} {name} ({r['seconds']}s)")
    print("go-aheads asked:", len(approver.asked))


if __name__ == "__main__":
    main()
