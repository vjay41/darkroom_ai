from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from darkroom_ai.config import CONFIG_ENV_VAR, load_config, parse_settings
from darkroom_ai.errors import DarkroomError, ErrorCode

REPO = Path(__file__).resolve().parents[2]

VALID: dict[str, Any] = {
    "llm": {
        "active_profile": "main",
        "profiles": {
            "main": {"base_url": "http://127.0.0.1:1234/v1", "model": "some/model", "vision": True}
        },
    }
}

MINIMAL_TOML = """
[llm]
active_profile = "main"
[llm.profiles.main]
base_url = "http://127.0.0.1:1234/v1"
model = "some/model"
"""


def _with(path: str, value: Any) -> dict[str, Any]:
    data = copy.deepcopy(VALID)
    node = data
    *parents, leaf = path.split(".")
    for key in parents:
        node = node.setdefault(key, {})
    node[leaf] = value
    return data


def test_repository_config_is_valid() -> None:
    settings = load_config(REPO / "darkroom.toml")
    assert set(settings.llm.profiles) == {"primary", "glimmer", "fast"}
    assert settings.llm.active is settings.llm.profiles["primary"]
    assert settings.guardrails.max_passes == 3
    assert settings.guardrails.exposure_ev_per_pass == 1.5


def test_defaults() -> None:
    settings = parse_settings(VALID)
    assert settings.lightroom.host == "127.0.0.1"
    assert settings.lightroom.test_catalog is None
    assert settings.photoshop.prog_id == "Photoshop.Application"
    assert settings.llm.active.tool_mode == "prompt"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://192.168.1.20:1234/v1",
        "http://127.0.0.1@evil.com/v1",
        "http://127.0.0.1.evil.com/v1",
        "http://0.0.0.0:1234/v1",
    ],
)
def test_non_local_base_url_rejected(url: str) -> None:
    with pytest.raises(DarkroomError) as caught:
        parse_settings(_with("llm.profiles.main.base_url", url))
    assert caught.value.code is ErrorCode.NON_LOCAL_ENDPOINT
    assert url in caught.value.message


def test_non_local_lightroom_host_rejected() -> None:
    with pytest.raises(DarkroomError) as caught:
        parse_settings(_with("lightroom.host", "192.168.1.5"))
    assert caught.value.code is ErrorCode.NON_LOCAL_ENDPOINT


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        ("llm.active_profile", "missing", "active_profile"),
        ("llm.profiles.main.tool_mode", "magic", "llm.profiles.main.tool_mode"),
        ("llm.profiles.main.model", "", "llm.profiles.main.model"),
        ("llm.profiles.main.unknown_key", 1, "llm.profiles.main.unknown_key"),
        ("guardrails.max_passes", 0, "guardrails.max_passes"),
        ("guardrails.exposure_ev_per_pass", 99, "guardrails.exposure_ev_per_pass"),
        ("lightroom.timeout_s", 0, "lightroom.timeout_s"),
        ("surprise", {}, "surprise"),
    ],
)
def test_invalid_values_rejected(path: str, value: Any, expected: str) -> None:
    with pytest.raises(DarkroomError) as caught:
        parse_settings(_with(path, value))
    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert expected in caught.value.message


def test_missing_llm_section_rejected() -> None:
    with pytest.raises(DarkroomError) as caught:
        parse_settings({})
    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DarkroomError) as caught:
        load_config(tmp_path / "nope.toml")
    assert caught.value.code is ErrorCode.CONFIG_INVALID
    assert "not found" in caught.value.message


def test_bad_toml(tmp_path: Path) -> None:
    path = tmp_path / "darkroom.toml"
    path.write_text("[llm\n", encoding="utf-8")
    with pytest.raises(DarkroomError) as caught:
        load_config(path)
    assert caught.value.code is ErrorCode.CONFIG_INVALID


def test_local_override_is_merged(tmp_path: Path) -> None:
    (tmp_path / "darkroom.toml").write_text(MINIMAL_TOML, encoding="utf-8")
    (tmp_path / "darkroom.local.toml").write_text(
        '[lightroom]\ntest_catalog = "D:/cat/Test.lrcat"\n[llm.profiles.main]\nvision = true\n',
        encoding="utf-8",
    )
    settings = load_config(tmp_path / "darkroom.toml")
    assert settings.lightroom.test_catalog == Path("D:/cat/Test.lrcat")
    assert settings.llm.active.vision is True
    assert settings.llm.active.model == "some/model"


def test_local_override_cannot_point_outside(tmp_path: Path) -> None:
    (tmp_path / "darkroom.toml").write_text(MINIMAL_TOML, encoding="utf-8")
    (tmp_path / "darkroom.local.toml").write_text(
        '[llm.profiles.main]\nbase_url = "https://example.com/v1"\n', encoding="utf-8"
    )
    with pytest.raises(DarkroomError) as caught:
        load_config(tmp_path / "darkroom.toml")
    assert caught.value.code is ErrorCode.NON_LOCAL_ENDPOINT


def test_env_var_selects_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "elsewhere.toml"
    path.write_text(MINIMAL_TOML, encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    assert load_config().llm.active_profile == "main"


def test_settings_are_immutable() -> None:
    settings = parse_settings(VALID)
    with pytest.raises(Exception, match="frozen"):
        settings.guardrails.max_passes = 9  # type: ignore[misc]
