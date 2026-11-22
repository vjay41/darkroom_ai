from __future__ import annotations

from pathlib import Path

import pytest

from darkroom_ai import __version__
from darkroom_ai.adapters.lightroom.models import PhotoInfo, Selection
from darkroom_ai.cli import EXIT_ERROR, EXIT_OK, collapsed_stack_warning, main

REPO = Path(__file__).resolve().parents[2]


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_OK
    assert "doctor" in capsys.readouterr().out


def test_doctor_reports_non_local_endpoint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "darkroom.toml"
    path.write_text(
        '[llm]\nactive_profile = "m"\n[llm.profiles.m]\n'
        'base_url = "https://api.example.com/v1"\nmodel = "x"\n',
        encoding="utf-8",
    )
    assert main(["--config", str(path), "doctor"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "[NON_LOCAL_ENDPOINT]" in err
    assert "Next step:" in err


def test_doctor_warns_only_when_the_selected_photo_is_in_a_collapsed_stack() -> None:
    def shown(**stack: bool | None) -> Selection:
        return Selection(
            active=PhotoInfo(id="p", file_name="a.jpg", file_format="JPG", **stack),
            photos=(),
        )

    assert collapsed_stack_warning(Selection()) is None
    assert collapsed_stack_warning(shown(in_stack=False, stack_collapsed=False)) is None
    assert collapsed_stack_warning(shown()) is None  # unknown is not a warning
    warning = collapsed_stack_warning(shown(in_stack=True, stack_collapsed=True))
    assert warning and "Expand All Stacks" in warning and "a.jpg" in warning
