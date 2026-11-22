import math

import cv2
import numpy as np
import pytest

from darkroom_ai.imaging.geometry import parse_aspect, plan_crop
from darkroom_ai.imaging.io import load_image, write_image
from darkroom_ai.imaging.scene import (
    find_candidates,
    fix_red_eye,
    inpaint,
    regions_mask,
    zone_map,
)


def _corners_inside(w: int, h: int, angle: float, plan) -> bool:  # type: ignore[no-untyped-def]
    cx, cy = plan.centre[0] * w, plan.centre[1] * h
    a, b = (plan.right - plan.left) * w, (plan.bottom - plan.top) * h
    t = math.radians(angle)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x = cx + sx * a / 2 * math.cos(t) - sy * b / 2 * math.sin(t)
            y = cy + sx * a / 2 * math.sin(t) + sy * b / 2 * math.cos(t)
            if not (-0.5 <= x <= w + 0.5 and -0.5 <= y <= h + 0.5):
                return False
    return True


@pytest.mark.parametrize("angle", [0.0, 1.0, -3.5, 10.0, 30.0])
@pytest.mark.parametrize("aspect", [1.5, 1.0, 0.8, 16 / 9])
def test_plan_crop_fits_and_is_tight(angle: float, aspect: float) -> None:
    plan = plan_crop(6000, 4000, angle, aspect)
    assert plan.fits_inside and _corners_inside(6000, 4000, angle, plan)
    got = (plan.right - plan.left) * 6000 / ((plan.bottom - plan.top) * 4000)
    assert got == pytest.approx(aspect, rel=1e-3)
    bigger = plan_crop(6000, 4000, angle, aspect)
    assert bigger.kept_fraction <= 1.0


def test_plan_crop_without_angle_keeps_frame_and_anchor_moves_it() -> None:
    full = plan_crop(6000, 4000, 0.0, 1.5)
    assert (full.left, full.top, full.right, full.bottom) == (0.0, 0.0, 1.0, 1.0)
    square = plan_crop(6000, 4000, 0.0, 1.0, anchor=(0.9, 0.5))
    assert square.right == pytest.approx(1.0, abs=1e-4)  # moved right, stays inside
    assert square.fits_inside
    assert plan_crop(6000, 4000, 5.0, 1.5).kept_fraction < 1.0


def test_parse_aspect() -> None:
    assert parse_aspect("3:2", 1.0) == 1.5
    assert parse_aspect("original", 1.25) == 1.25
    with pytest.raises(ValueError):
        parse_aspect("wide", 1.0)
    with pytest.raises(ValueError):
        parse_aspect("1:9", 1.0)


def test_zone_map_finds_hard_edged_shadow() -> None:
    img = np.full((600, 800, 3), 0.7, np.float32)
    img[200:500, 100:400] = 0.05
    result = zone_map(img)
    assert len(result.cells) == 48
    dark = result.dark_regions[0]
    assert dark.mean_luma < 0.1 and dark.surround_luma > 0.4
    assert dark.box[0] == pytest.approx(0.125, abs=0.05) and dark.box[1] == pytest.approx(
        0.33, abs=0.05
    )
    assert any(c.zone <= 1 for c in result.cells) and any(c.zone >= 6 for c in result.cells)


def test_find_candidates_sees_an_odd_object() -> None:
    rng = np.random.default_rng(1)
    img = np.clip(0.5 + rng.normal(0, 0.01, (600, 800, 3)), 0, 1).astype(np.float32)
    cv2.circle(img, (600, 300), 30, (0.9, 0.1, 0.1), -1)
    found = find_candidates(img)
    assert found
    top = found[0]
    assert top.centre[0] == pytest.approx(0.75, abs=0.05) and top.centre[1] == pytest.approx(
        0.5, abs=0.06
    )
    assert top.delta_e_vs_surround > 20 and not top.touches_border
    assert find_candidates(np.full((300, 400, 3), 0.5, np.float32)) == ()


def test_inpaint_changes_only_masked_pixels() -> None:
    img = np.full((300, 400, 3), 0.4, np.float32)
    img[:, :] += np.linspace(0, 0.2, 400, dtype=np.float32)[None, :, None]
    marked = img.copy()
    cv2.circle(marked, (200, 150), 12, (1.0, 0.0, 0.0), -1)
    mask = regions_mask((300, 400), [{"box": [0.45, 0.4, 0.55, 0.6], "shape": "ellipse"}], 6)
    out = inpaint(marked, mask)
    assert np.abs(out - img)[mask > 0].max() < 0.05  # the red dot is gone
    assert np.array_equal(out[mask == 0], marked[mask == 0])


def test_red_eye_fix() -> None:
    img = np.full((200, 300, 3), 0.4, np.float32)
    cv2.circle(img, (100, 100), 8, (0.9, 0.1, 0.1), -1)
    fixed, shares = fix_red_eye(img, [(0.25, 0.4, 0.42, 0.6)])
    assert shares[0] > 0.05
    assert fixed[100, 100, 0] < 0.3
    assert np.array_equal(fixed[:, 200:], img[:, 200:])


def test_write_image_roundtrip_and_no_overwrite(tmp_path) -> None:  # type: ignore[no-untyped-def]
    img = np.random.default_rng(0).random((20, 30, 3)).astype(np.float32)
    path = write_image(tmp_path / "a.tif", img, bits=16)
    assert np.abs(load_image(path) - img).max() < 1e-4
    with pytest.raises(Exception, match="overwrite"):
        write_image(path, img)
    with pytest.raises(Exception, match="16-bit"):
        write_image(tmp_path / "b.jpg", img, bits=16)
