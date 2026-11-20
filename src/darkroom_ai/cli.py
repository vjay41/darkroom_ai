"""Command line entry point (`darkroom`).

So far: `--version`, a `doctor` that checks the configuration only, and `lightroom setup|ping`.
Chat, model tests and the full doctor arrive in phase 6.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from darkroom_ai import __version__
from darkroom_ai.adapters.lightroom import plugin_dir
from darkroom_ai.adapters.lightroom.bridge_file import (
    bridge_path,
    load_bridge_file,
    write_bridge_file,
)
from darkroom_ai.adapters.lightroom.client import LightroomClient
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


def _lightroom_setup(config_path: Path | None, rotate_token: bool) -> int:
    settings = load_config(config_path)
    bridge = write_bridge_file(settings, rotate_token=rotate_token)
    print(f"bridge file written: {bridge_path()}")
    print(f"  catalog the plug-in may write to: {bridge.catalog_path}")
    print(f"  preview folder: {bridge.preview_dir}")
    print(f"  localhost ports: {bridge.request_port} (requests), {bridge.response_port} (replies)")
    print("In Lightroom: File > Plug-in Manager > Add, and choose this folder:")
    print(f"  {plugin_dir()}")
    print("If the plug-in is already added, press 'Reload Plug-in' there.")
    return EXIT_OK


def _lightroom_ping(config_path: Path | None) -> int:
    settings = load_config(config_path)
    with LightroomClient.from_settings(settings, load_bridge_file()) as client:
        info = client.ping()
    print(f"Lightroom {info.lightroom_version}, plug-in {info.plugin_version}")
    print(f"  open catalog: {info.catalog_path}")
    print(f"  configured catalog: {info.configured_catalog}")
    print(f"  writes allowed: {'yes' if info.writes_allowed else 'NO (different catalog open)'}")
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
    lightroom = commands.add_parser("lightroom", help="Lightroom plug-in setup and checks")
    lightroom_commands = lightroom.add_subparsers(dest="lightroom_command", required=True)
    setup = lightroom_commands.add_parser("setup", help="write the plug-in's bridge.json")
    setup.add_argument("--rotate-token", action="store_true", help="generate a new token")
    lightroom_commands.add_parser("ping", help="check that the plug-in answers")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor(args.config)
        if args.command == "lightroom":
            if args.lightroom_command == "setup":
                return _lightroom_setup(args.config, args.rotate_token)
            return _lightroom_ping(args.config)
    except DarkroomError as exc:
        print(f"[{exc.code.value}] {exc.message}", file=sys.stderr)
        print(f"Next step: {exc.next_step}", file=sys.stderr)
        return EXIT_ERROR
    parser.print_help()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
