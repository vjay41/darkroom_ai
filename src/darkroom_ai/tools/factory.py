"""Builds the real tool registry from settings (Lightroom plug-in, Photoshop, masks, journal)."""

from __future__ import annotations

from darkroom_ai.adapters.lightroom.bridge_file import load_bridge_file
from darkroom_ai.adapters.lightroom.client import LightroomClient
from darkroom_ai.adapters.photoshop.client import PhotoshopClient
from darkroom_ai.adapters.photoshop.masks import PhotoshopSelectSubjectProvider
from darkroom_ai.adapters.processes import WindowsProcesses
from darkroom_ai.config import Settings
from darkroom_ai.imaging.providers import build_face_detector, build_mask_provider
from darkroom_ai.safety.confirm import Confirmer, DenyAll
from darkroom_ai.safety.journal import JsonlJournal
from darkroom_ai.tools.context import ToolContext
from darkroom_ai.tools.registry import ToolRegistry

JOURNAL_FILE = "journal.jsonl"


def build_tool_context(
    settings: Settings, *, confirmer: Confirmer | None = None, session: str | None = None
) -> ToolContext:
    """Connect the tools to the real apps. Nothing is contacted until a tool is called.

    `session` resumes an earlier journal session (for a script that pauses between a run and
    its undo). It is chosen by the program that builds the context, never by a model.
    """
    lightroom = LightroomClient.from_settings(settings, load_bridge_file())
    photoshop = PhotoshopClient.from_settings(settings)
    masks = build_mask_provider(
        settings, photoshop=lambda: PhotoshopSelectSubjectProvider(photoshop)
    )
    return ToolContext(
        settings=settings,
        lightroom=lightroom,
        journal=JsonlJournal(settings.paths.logs_dir.resolve() / JOURNAL_FILE, session=session),
        confirmer=confirmer or DenyAll(),
        photoshop=photoshop,
        masks=masks,
        faces=build_face_detector(settings),
        processes=WindowsProcesses(),
    )


def build_tool_registry(settings: Settings, *, confirmer: Confirmer | None = None) -> ToolRegistry:
    return ToolRegistry(build_tool_context(settings, confirmer=confirmer))
