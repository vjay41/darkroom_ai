import cv2
import numpy as np
import pytest

from darkroom_ai.imaging.tilt import measure_tilt


def _scene(angle_deg: float) -> np.ndarray:
    img = np.full((800, 1200, 3), 255, np.uint8)
    for y in (200, 400, 600):
        cv2.line(img, (100, y), (1100, y), (0, 0, 0), 3)
    for x in (300, 600, 900):
        cv2.line(img, (x, 100), (x, 700), (0, 0, 0), 3)
    matrix = cv2.getRotationMatrix2D(
        (600, 400), -angle_deg, 1.0
    )  # cv2 positive = counter-clockwise
    img = cv2.warpAffine(img, matrix, (1200, 800), borderValue=(255, 255, 255))
    return img.astype(np.float32) / 255.0


@pytest.mark.parametrize("angle", [-3.0, 0.0, 2.0, 5.0])
def test_tilt_recovered_in_both_families(angle: float) -> None:
    reading = measure_tilt(_scene(angle))
    assert reading.horizontal and reading.vertical
    assert reading.horizontal.tilt_deg == pytest.approx(angle, abs=0.4)
    assert reading.vertical.tilt_deg == pytest.approx(angle, abs=0.4)
    assert reading.families_agree is True


def test_blank_image_has_no_lines() -> None:
    reading = measure_tilt(np.full((300, 400, 3), 0.5, np.float32))
    assert reading.horizontal is None and reading.vertical is None
    assert reading.families_agree is None
