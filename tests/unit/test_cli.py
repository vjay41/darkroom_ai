from __future__ import annotations

from pathlib import Path

import pytest

from darkroom_ai import __version__
from darkroom_ai.cli import EXIT_ERROR, EXIT_OK, main

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
