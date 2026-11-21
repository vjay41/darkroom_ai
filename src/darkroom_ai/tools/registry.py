"""The tool registry: one list of typed tools, shared by the agent and the MCP server.

Every call goes through `ToolRegistry.call`, which validates the arguments, runs the tool and
writes exactly one journal entry (also for failures).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from darkroom_ai.errors import DarkroomError, ErrorCode
from darkroom_ai.tools import (
    app_status,
    background,
    capabilities,
    catalog_tools,
    lightroom_tools,
    masks,
)
from darkroom_ai.tools.context import Outcome, ToolContext


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], Outcome]
    writes: bool

    def schema(self) -> dict[str, Any]:
        """Name, description and JSON schema of the arguments (for the model)."""
        parameters = self.input_model.model_json_schema()
        parameters.pop("title", None)
        parameters.pop("description", None)
        return {"name": self.name, "description": self.description, "parameters": parameters}


def _tool(
    name: str, model: type[BaseModel], handler: Callable[[ToolContext, Any], Outcome], writes: bool
) -> Tool:
    description = " ".join((model.__doc__ or "").split())
    return Tool(name, description, model, handler, writes)


TOOLS: tuple[Tool, ...] = (
    _tool(
        "get_app_status",
        app_status.GetAppStatusInput,
        app_status.get_app_status,
        writes=False,
    ),
    _tool(
        "get_capabilities",
        capabilities.GetCapabilitiesInput,
        capabilities.get_capabilities,
        writes=False,
    ),
    _tool(
        "run_selftest",
        capabilities.RunSelftestInput,
        capabilities.run_selftest_tool,
        writes=False,
    ),
    _tool(
        "ensure_apps_running",
        app_status.EnsureAppsRunningInput,
        app_status.ensure_apps_running,
        writes=False,
    ),
    _tool(
        "get_photo_info",
        lightroom_tools.GetPhotoInfoInput,
        lightroom_tools.get_photo_info,
        writes=False,
    ),
    _tool(
        "list_catalog_photos",
        lightroom_tools.ListCatalogPhotosInput,
        lightroom_tools.list_catalog_photos,
        writes=False,
    ),
    _tool(
        "catalog_summary",
        catalog_tools.CatalogSummaryInput,
        catalog_tools.catalog_summary,
        writes=False,
    ),
    _tool("scan_folder", catalog_tools.ScanFolderInput, catalog_tools.scan_folder, writes=False),
    _tool(
        "import_folder", catalog_tools.ImportFolderInput, catalog_tools.import_folder, writes=True
    ),
    _tool(
        "get_photo_metadata",
        lightroom_tools.GetPhotoMetadataInput,
        lightroom_tools.get_photo_metadata,
        writes=False,
    ),
    _tool(
        "get_selected_photos",
        lightroom_tools.GetSelectedPhotosInput,
        lightroom_tools.get_selected_photos,
        writes=False,
    ),
    _tool(
        "find_photo",
        lightroom_tools.FindPhotoInput,
        lightroom_tools.find_photo,
        writes=False,
    ),
    _tool(
        "get_preview",
        lightroom_tools.GetPreviewInput,
        lightroom_tools.get_preview,
        writes=False,
    ),
    _tool(
        "analyze_photo",
        lightroom_tools.AnalyzePhotoInput,
        lightroom_tools.analyze_photo,
        writes=False,
    ),
    _tool(
        "analyze_batch",
        lightroom_tools.AnalyzeBatchInput,
        lightroom_tools.analyze_batch,
        writes=False,
    ),
    _tool(
        "adjust_lighting",
        lightroom_tools.AdjustLightingInput,
        lightroom_tools.adjust_lighting,
        writes=True,
    ),
    _tool(
        "adjust_white_balance",
        lightroom_tools.AdjustWhiteBalanceInput,
        lightroom_tools.adjust_white_balance,
        writes=True,
    ),
    _tool(
        "list_develop_parameters",
        lightroom_tools.ListDevelopParametersInput,
        lightroom_tools.list_develop_parameters,
        writes=False,
    ),
    _tool(
        "get_develop_parameters",
        lightroom_tools.GetDevelopParametersInput,
        lightroom_tools.get_develop_parameters,
        writes=False,
    ),
    _tool(
        "set_develop_parameters",
        lightroom_tools.SetDevelopParametersInput,
        lightroom_tools.set_develop_parameters,
        writes=True,
    ),
    _tool("list_masks", masks.ListMasksInput, masks.list_masks, writes=False),
    _tool("create_mask", masks.CreateMaskInput, masks.create_mask, writes=True),
    _tool("inspect_masks", masks.InspectMasksInput, masks.inspect_masks, writes=False),
    _tool(
        "set_mask_adjustments",
        masks.SetMaskAdjustmentsInput,
        masks.set_mask_adjustments,
        writes=True,
    ),
    _tool("invert_mask", masks.InvertMaskInput, masks.invert_mask, writes=True),
    _tool("delete_mask", masks.DeleteMaskInput, masks.delete_mask, writes=True),
    _tool("adjust_hsl", lightroom_tools.AdjustHslInput, lightroom_tools.adjust_hsl, writes=True),
    _tool("crop", lightroom_tools.CropInput, lightroom_tools.crop, writes=True),
    _tool(
        "replace_background",
        background.ReplaceBackgroundInput,
        background.replace_background,
        writes=True,
    ),
    _tool("undo", lightroom_tools.UndoInput, lightroom_tools.undo, writes=True),
    _tool(
        "list_presets",
        lightroom_tools.ListPresetsInput,
        lightroom_tools.list_presets,
        writes=False,
    ),
    _tool(
        "apply_preset", lightroom_tools.ApplyPresetInput, lightroom_tools.apply_preset, writes=True
    ),
)


UNTESTED_TOOLS: tuple[Tool, ...] = ()


class ToolRegistry:
    def __init__(self, context: ToolContext, tools: tuple[Tool, ...] = TOOLS) -> None:
        self._context = context
        self._tools = {tool.name: tool for tool in tools}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Validate, run and journal one tool call. Returns the result as plain JSON data."""
        journal = self._context.journal
        raw_args = dict(arguments)
        tool = self._tools.get(name)
        if tool is None:
            error = DarkroomError(
                ErrorCode.LLM_BAD_TOOL_CALL,
                f"There is no tool named '{name}'.",
                details={"known_tools": list(self._tools)},
            )
            journal.record(name, raw_args, status="error", error=error.to_dict())
            raise error
        guard = self._context.connector
        if (
            guard is not None
            and tool.writes
            and guard.stale
            and self._context.settings.runtime.refuse_writes_when_stale
        ):
            error = DarkroomError(
                ErrorCode.CONNECTOR_STALE,
                f"'{name}' changes things, but this connector is running older code than the "
                f"files on disk (started {guard.started_at}). Nothing was changed.",
                details={"running_code": guard.version, "code_on_disk": guard.on_disk()},
            )
            journal.record(name, raw_args, status="error", error=error.to_dict())
            raise error
        try:
            args = tool.input_model.model_validate(raw_args)
        except ValidationError as exc:
            problems = [
                f"{'.'.join(str(part) for part in err['loc']) or '(arguments)'}: {err['msg']}"
                for err in exc.errors()
            ]
            error = DarkroomError(
                ErrorCode.LLM_BAD_TOOL_CALL,
                f"The arguments for '{name}' are not valid: " + "; ".join(problems),
                details={"problems": problems},
            )
            journal.record(name, raw_args, status="error", error=error.to_dict())
            raise error from exc

        try:
            outcome = tool.handler(self._context, args)
        except DarkroomError as exc:
            journal.record(name, raw_args, status="error", error=exc.to_dict())
            raise
        entry = journal.record(name, raw_args, applied=outcome.applied, undo=outcome.undo)
        result = outcome.result.model_copy(update={"journal_id": entry.id})
        data = result.model_dump(mode="json")
        warning = guard.warning() if guard is not None else None
        if warning:
            data["connector_warning"] = warning
        return data
