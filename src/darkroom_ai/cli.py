"""Command line entry point (`darkroom`).

Phase 0 skeleton: `--version` and a `doctor` that checks the configuration only. Chat, model tests
and the full doctor arrive in phase 6.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from darkroom_ai import __version__
from darkroom_ai.config import load_config
from darkroom_ai.errors import DarkroomError

EXIT_OK = 0
EXIT_ERROR = 2


def _doctor(config_path: Path | None) -> int:
    settings = load_config(config_path)
    profile = settings.llm.active
    print("config: OK")
    print(
        f"  active profile: {settings.llm.active_profile} -> {profile.model} at {profile.base_url}"
    )
    print(f"  profiles: {', '.join(sorted(settings.llm.profiles))}")
    print(f"  lightroom test catalog: {settings.lightroom.test_catalog or '(not set)'}")
    print("not checked yet (later phases): Lightroom, Photoshop, model server, mask model")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="darkroom",
        description="Local, offline AI photo-editing agent for Lightroom Classic and Photoshop.",
    )
    parser.add_argument("--version", action="version", version=f"darkroom {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="path to darkroom.toml")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("doctor", help="check the local setup")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor(args.config)
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}", file=sys.stderr)
        print(f"Next step: {exc.next_step}", file=sys.stderr)
        return EXIT_ERROR
    parser.print_help()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
