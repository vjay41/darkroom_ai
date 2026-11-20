"""Lightroom Classic adapter: our own Lua plug-in plus the Python client that talks to it."""

from pathlib import Path

PLUGIN_DIR_NAME = "darkroom.lrplugin"


def plugin_dir() -> Path:
    """Folder of the Lightroom plug-in shipped with this package (add it in the Plug-in Manager)."""
    return Path(__file__).resolve().parent / "plugin" / PLUGIN_DIR_NAME
