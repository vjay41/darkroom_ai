"""Photoshop adapter: COM (pywin32) in a child process with a deadline, plus ExtendScript files."""

from pathlib import Path


def jsx_dir() -> Path:
    """Folder with the ExtendScript files shipped with this package."""
    return Path(__file__).resolve().parent / "jsx"


def read_jsx(name: str) -> str:
    return (jsx_dir() / name).read_text(encoding="utf-8")
