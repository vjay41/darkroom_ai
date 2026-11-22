"""Imaging toolkit tests on synthetic images with known answers (no real photos, no GPU)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.imaging.analysis import analyze
from darkroom_ai.imaging.faces import FaceBox, YuNetFaceDetector, analyze_faces
from darkroom_ai.imaging.io import load_image, load_mask, save_mask_png
from darkroom_ai.imaging.masks import (
    MaskProvider,
    OnnxMaskProvider,
    SyntheticMaskProvider,
    mask_metrics,
)
from darkroom_ai.interfaces import FaceDetector


def grey(height: int = 100, width: int = 200, value: float = 0.5) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.float32)


def write_png(path: Path, pixels: np.ndarray) -> Path:
    bgr = cv2.cvtColor(np.rint(pixels * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
    assert cv2.imwrite(str(path), bgr)
    return path


# -- analysis -------------------------------------------------------------------------------------


def test_flat_grey_has_no_clipping_and_no_cast() -> None:
    result = analyze(grey(value=0.5))
    assert (result.width, result.height) == (200, 100)
    assert result.tones.mean == pytest.approx(0.5, abs=1e-3)
    assert result.tones.p50 == pytest.approx(0.5, abs=1e-3)
    assert result.clipping.highlights_pct == 0
    assert result.clipping.shadows_pct == 0
    assert result.color_cast.confidence == "ok"
    assert result.color_cast.strength == pytest.approx(0, abs=1e-4)
    assert result.color_cast.description == "no noticeable cast"
    assert sum(result.tones.histogram) == pytest.approx(1.0, abs=1e-3)


def test_known_clipping_fractions() -> None:
    pixels = grey(height=100, width=100, value=0.5)
    pixels[:10, :, :] = 1.0  # 10% blown highlights
    pixels[10:15, :, :] = 0.0  # 5% crushed shadows
    pixels[15:17, :, 0] = 1.0  # 2% red channel only
    result = analyze(pixels)
    assert result.clipping.highlights_pct == pytest.approx(12.0, abs=0.01)
    assert result.clipping.shadows_pct == pytest.approx(5.0, abs=0.01)
    assert result.clipping.red_high_pct == pytest.approx(12.0, abs=0.01)
    assert result.clipping.green_high_pct == pytest.approx(10.0, abs=0.01)


@pytest.mark.parametrize(
    ("shift", "axis", "sign", "word"),
    [
        ((0.04, 0.04, -0.04), "blue_yellow", 1, "yellow/warm"),
        ((-0.04, -0.04, 0.04), "blue_yellow", -1, "blue/cool"),
        ((0.03, -0.04, 0.03), "green_magenta", 1, "magenta"),
        ((-0.03, 0.04, -0.03), "green_magenta", -1, "green"),
    ],
)
def test_injected_colour_cast_is_found(
    shift: tuple[float, float, float], axis: str, sign: int, word: str
) -> None:
    pixels = grey(value=0.5) + np.array(shift, dtype=np.float32)
    cast = analyze(pixels).color_cast
    assert cast.confidence == "ok"
    assert np.sign(getattr(cast, axis)) == sign
    assert abs(getattr(cast, axis)) > 0.05
    assert word in cast.description


def test_saturated_image_gives_low_confidence_cast() -> None:
    pixels = np.zeros((50, 50, 3), dtype=np.float32)
    pixels[..., 0] = 0.9  # pure red: no near-neutral pixels
    cast = analyze(pixels).color_cast
    assert cast.confidence == "low"
    assert cast.strength is None
    assert "not enough" in cast.description


def test_gradient_percentiles() -> None:
    ramp = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    pixels = np.repeat(np.tile(ramp, (10, 1))[:, :, np.newaxis], 3, axis=2)
    tones = analyze(pixels).tones
    assert tones.p05 == pytest.approx(0.05, abs=0.01)
    assert tones.p50 == pytest.approx(0.5, abs=0.01)
    assert tones.p95 == pytest.approx(0.95, abs=0.01)


def test_analyze_rejects_wrong_shapes() -> None:
    with pytest.raises(ValueError, match="H x W x 3"):
        analyze(np.zeros((10, 10), dtype=np.float32))


# -- io -------------------------------------------------------------------------------------------


def test_load_8_bit_and_16_bit_images(tmp_path: Path) -> None:
    pixels = grey(20, 30, 0.25)
    pixels[:, :10, 0] = 0.75
    eight = load_image(write_png(tmp_path / "a.png", pixels))
    assert eight.shape == (20, 30, 3)
    assert eight[0, 0, 0] == pytest.approx(0.75, abs=0.01)  # red stays red (RGB order)
    assert eight[0, 20, 0] == pytest.approx(0.25, abs=0.01)

    sixteen_path = tmp_path / "b.tif"
    bgr16 = cv2.cvtColor(np.rint(pixels * 65535).astype(np.uint16), cv2.COLOR_RGB2BGR)
    assert cv2.imwrite(str(sixteen_path), bgr16)
    sixteen = load_image(sixteen_path)
    assert sixteen.dtype == np.float32
    assert sixteen[0, 0, 0] == pytest.approx(0.75, abs=0.001)


def test_unreadable_images_are_reported(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    for path in (tmp_path / "missing.jpg", bad):
        with pytest.raises(DarkroomError) as caught:
            load_image(path)
        assert caught.value.code is ErrorCode.IMAGE_UNREADABLE


def test_mask_png_round_trip_and_no_overwrite(tmp_path: Path) -> None:
    mask = np.zeros((10, 20), dtype=np.float32)
    mask[2:8, 5:15] = 1.0
    path = save_mask_png(mask, tmp_path / "out" / "m.png")
    assert np.array_equal(load_mask(path), mask)
    with pytest.raises(DarkroomError) as caught:
        save_mask_png(mask, path)
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED


# -- faces ----------------------------------------------------------------------------------------


class FixedFaces:
    name = "fixed"

    def __init__(self, boxes: list[FaceBox]) -> None:
        self._boxes = boxes

    def detect(self, pixels: np.ndarray) -> list[FaceBox]:
        return self._boxes


def test_face_analysis_without_a_model_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(DarkroomError) as caught:
        analyze_faces(grey(), None)
    assert caught.value.code is ErrorCode.FACE_MODEL_MISSING
    assert "downloaded" in caught.value.next_step
    with pytest.raises(DarkroomError) as caught:
        YuNetFaceDetector(tmp_path / "no_such_model.onnx")
    assert caught.value.code is ErrorCode.FACE_MODEL_MISSING


def test_skin_level_is_measured_inside_the_face_box() -> None:
    pixels = grey(value=0.2)
    pixels[20:60, 40:80, :] = 0.6  # a flat "face" patch
    detector: FaceDetector = FixedFaces([FaceBox(x=40, y=20, width=40, height=40, score=0.99)])
    result = analyze_faces(pixels, detector)
    assert result.detector == "fixed"
    assert len(result.faces) == 1
    assert result.faces[0].skin_luma_mean == pytest.approx(0.6, abs=1e-3)
    assert analyze_faces(pixels, FixedFaces([])).faces == ()


# -- masks ----------------------------------------------------------------------------------------


def test_synthetic_provider_writes_a_same_size_mask(tmp_path: Path) -> None:
    image = write_png(tmp_path / "photo.png", grey(120, 200))
    provider: MaskProvider = SyntheticMaskProvider()
    result = provider.subject_mask(image, tmp_path / "mask.png")
    assert result.provider == "synthetic"
    assert (result.metrics.width, result.metrics.height) == (200, 120)
    assert load_mask(result.path).shape == (120, 200)
    # Ellipse with half-axes w/4 and 0.3 h covers pi * 0.25 * 0.3 = 23.6% of the frame.
    assert result.metrics.coverage_pct == pytest.approx(23.6, abs=1.5)
    assert result.metrics.region_count == 1
    assert not result.metrics.is_blank
    with pytest.raises(DarkroomError) as caught:
        provider.subject_mask(image, tmp_path / "mask.png")
    assert caught.value.code is ErrorCode.GUARDRAIL_REJECTED


def test_metrics_of_a_circle_and_of_a_ragged_shape() -> None:
    circle = np.zeros((400, 400), dtype=np.float32)
    cv2.circle(circle, (200, 200), 100, 1.0, thickness=-1)
    smooth = mask_metrics(circle)
    assert smooth.coverage_pct == pytest.approx(np.pi * 100 * 100 / 160000 * 100, abs=0.5)
    assert smooth.region_count == 1
    assert smooth.compactness == pytest.approx(1.0, abs=0.15)
    assert smooth.hull_perimeter_ratio == pytest.approx(1.0, abs=0.08)
    assert smooth.solidity > 0.97
    assert smooth.soft_edge_pct == 0

    star = np.zeros((400, 400), dtype=np.float32)
    points = []
    for i in range(24):
        radius = 150 if i % 2 == 0 else 60
        angle = i * np.pi / 12
        points.append([200 + radius * np.cos(angle), 200 + radius * np.sin(angle)])
    cv2.fillPoly(star, [np.array(points, dtype=np.int32)], 1.0)
    ragged = mask_metrics(star)
    assert ragged.compactness > 2 * smooth.compactness
    assert ragged.hull_perimeter_ratio > 1.5
    assert ragged.solidity < 0.7


def test_metrics_count_regions_specks_and_blank_masks() -> None:
    mask = np.zeros((200, 200), dtype=np.float32)
    mask[10:60, 10:60] = 1.0
    mask[120:180, 120:180] = 1.0
    mask[100, 5] = 1.0  # a single-pixel speck
    metrics = mask_metrics(mask)
    assert metrics.region_count == 2
    assert metrics.speck_count == 1
    assert mask_metrics(np.zeros((50, 50), dtype=np.float32)).is_blank
    assert mask_metrics(np.ones((50, 50), dtype=np.float32)).is_blank


class FakeSession:
    """Stands in for an onnxruntime session: marks the left half of the input as subject."""

    def __init__(self, logits: bool = False) -> None:
        self.logits = logits
        self.seen: list[np.ndarray] = []

    def get_inputs(self) -> list[Any]:
        return [type("Input", (), {"name": "input"})()]

    def run(self, _: Any, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        tensor = feeds["input"]
        self.seen.append(tensor)
        _, _, height, width = tensor.shape
        output = np.zeros((1, 1, height, width), dtype=np.float32)
        output[..., : width // 2] = 1.0
        return [output * 12.0 - 6.0 if self.logits else output]


@pytest.mark.parametrize("logits", [False, True])
def test_onnx_provider_pre_and_post_processing(tmp_path: Path, logits: bool) -> None:
    model = tmp_path / "model.onnx"
    model.write_bytes(b"fake")
    session = FakeSession(logits=logits)
    calls: list[tuple[Path, int, bool]] = []

    def factory(path: Path, gpu_index: int, allow_cpu: bool) -> FakeSession:
        calls.append((path, gpu_index, allow_cpu))
        return session

    provider = OnnxMaskProvider(model, input_size=(64, 32), session_factory=factory)
    image = write_png(tmp_path / "photo.png", grey(90, 160))
    result = provider.subject_mask(image, tmp_path / "mask.png")

    assert calls == [(model, 1, False)]  # GPU 1 by default, no CPU fall-back
    assert session.seen[0].shape == (1, 3, 32, 64)  # NCHW at the model's input size
    assert session.seen[0].dtype == np.float32
    assert (result.metrics.width, result.metrics.height) == (160, 90)  # back at image size
    assert result.metrics.coverage_pct == pytest.approx(50.0, abs=2.0)
    mask = load_mask(result.path)
    assert mask[45, 10] > 0.9 and mask[45, 150] < 0.1


def test_onnx_provider_needs_a_model_file(tmp_path: Path) -> None:
    for path in (None, tmp_path / "missing.onnx"):
        with pytest.raises(DarkroomError) as caught:
            OnnxMaskProvider(path)
        assert caught.value.code is ErrorCode.MASK_MODEL_MISSING


def test_onnx_provider_reports_a_missing_runtime(tmp_path: Path) -> None:
    import importlib.util

    if importlib.util.find_spec("onnxruntime") is not None:
        pytest.skip("onnxruntime is installed; the missing-package path cannot be exercised")
    model = tmp_path / "model.onnx"
    model.write_bytes(b"fake")
    image = write_png(tmp_path / "photo.png", grey(20, 20))
    with pytest.raises(DarkroomError) as caught:
        OnnxMaskProvider(model).subject_mask(image, tmp_path / "mask.png")
    assert caught.value.code is ErrorCode.MASK_MODEL_MISSING
    assert "install-ml" in caught.value.message


# -- providers from settings ----------------------------------------------------------------------


def _settings(**sections: Any) -> Any:
    from darkroom_ai.config import parse_settings

    data: dict[str, Any] = {
        "llm": {
            "active_profile": "m",
            "profiles": {"m": {"base_url": "http://127.0.0.1:1234/v1", "model": "x"}},
        }
    }
    data.update(sections)
    return parse_settings(data)


def test_default_backend_is_photoshop_and_needs_the_adapter() -> None:
    from darkroom_ai.imaging.providers import build_mask_provider

    settings = _settings()
    assert settings.masks.backend == "photoshop"
    assert settings.masks.gpu_index == 1
    assert settings.masks.allow_cpu is False
    with pytest.raises(DarkroomError) as caught:
        build_mask_provider(settings)
    assert caught.value.code is ErrorCode.MASK_FAILED
    sentinel = SyntheticMaskProvider()
    assert build_mask_provider(settings, photoshop=lambda: sentinel) is sentinel


def test_backend_selection_and_missing_onnx_model(tmp_path: Path) -> None:
    from darkroom_ai.imaging.providers import build_face_detector, build_mask_provider

    assert build_mask_provider(_settings(masks={"backend": "synthetic"})).name == "synthetic"
    with pytest.raises(DarkroomError) as caught:
        build_mask_provider(_settings(masks={"backend": "onnx"}))
    assert caught.value.code is ErrorCode.MASK_MODEL_MISSING

    model = tmp_path / "m.onnx"
    model.write_bytes(b"fake")
    provider = build_mask_provider(_settings(masks={"backend": "onnx", "onnx_model": str(model)}))
    assert provider.name == "onnx"

    assert build_face_detector(_settings()) is None
    with pytest.raises(DarkroomError) as caught:
        build_face_detector(_settings(faces={"model": str(tmp_path / "missing.onnx")}))
    assert caught.value.code is ErrorCode.FACE_MODEL_MISSING


def test_unknown_mask_backend_is_rejected() -> None:
    with pytest.raises(DarkroomError) as caught:
        _settings(masks={"backend": "cloud"})
    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_cast_that_cannot_be_judged_is_null_with_a_reason_never_zero() -> None:
    # Strongly coloured picture: no near-neutral pixels at all.
    pixels = np.zeros((40, 60, 3), dtype=np.float32)
    pixels[..., 0] = 0.9
    pixels[..., 2] = 0.1
    cast = analyze(pixels).color_cast
    assert cast.measurable is False
    assert cast.blue_yellow is None and cast.green_magenta is None and cast.strength is None
    assert cast.confidence == "low"
    assert cast.description.startswith("not measurable")
    assert "near-neutral" in cast.reason and "Unknown is not the same as neutral" in cast.reason
    data = cast.model_dump(mode="json")
    assert data["blue_yellow"] is None  # serialised as null, not 0.0

    grey = np.full((40, 60, 3), 0.5, dtype=np.float32)
    neutral = analyze(grey).color_cast
    assert neutral.measurable is True and neutral.strength == 0 and neutral.reason == ""


# -- noise and edge strength ----------------------------------------------------------------------


def noisy(sigma: float, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = np.full((256, 256, 3), 0.5, dtype=np.float32)
    return np.clip(base + rng.normal(0.0, sigma, base.shape).astype(np.float32), 0, 1)


def test_noise_estimate_follows_the_added_noise() -> None:
    low = analyze(noisy(0.01)).detail.noise_luma
    high = analyze(noisy(0.04)).detail.noise_luma
    assert low is not None and high is not None
    assert high > 3 * low  # four times the noise reads as clearly more
    true_level = 0.04 * 255  # the flattest pixels read a little low, never high
    assert 0.5 * true_level < high < 1.1 * true_level


def test_noise_ignores_edges_and_chroma_noise_is_separate() -> None:
    pixels = np.zeros((256, 256, 3), dtype=np.float32) + 0.3
    pixels[:, 128:] = 0.7  # one hard edge, no noise
    quiet = analyze(pixels).detail
    assert quiet.noise_luma is not None and quiet.noise_luma < 0.3
    rng = np.random.default_rng(2)
    colourful = np.full((256, 256, 3), 0.5, dtype=np.float32)
    colourful[..., 0] += rng.normal(0, 0.03, (256, 256)).astype(np.float32)
    colourful = np.clip(colourful, 0, 1)
    detail = analyze(colourful).detail
    assert detail.noise_chroma is not None and detail.noise_luma is not None
    assert detail.noise_chroma > detail.noise_luma


def test_no_flat_area_means_no_noise_number() -> None:
    black = np.zeros((64, 64, 3), dtype=np.float32)
    detail = analyze(black).detail
    assert detail.noise_luma is None and detail.flat_fraction == 0.0


def test_blurring_lowers_edge_strength() -> None:
    import cv2

    pixels = np.zeros((256, 256, 3), dtype=np.float32) + 0.2
    pixels[:, 100:156] = 0.8
    sharp = analyze(pixels).detail.edge_strength
    soft = analyze(cv2.GaussianBlur(pixels, (0, 0), 4)).detail.edge_strength
    assert soft < sharp * 0.6
