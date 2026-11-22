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
    action_tools,
    app_status,
    auto_tools,
    background,
    batch,
    capabilities,
    catalog_management,
    catalog_search_tools,
    catalog_tools,
    composition_tools,
    delivery_tools,
    library_tools,
    lightroom_tools,
    masks,
    preset_tools,
    region_tools,
    retouch_tools,
    scene_tools,
    sync_tools,
    tilt_tools,
    tone_curve,
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
    _tool(
        "list_catalogs",
        catalog_management.ListCatalogsInput,
        catalog_management.list_catalogs,
        writes=False,
    ),
    _tool(
        "get_catalog_info",
        catalog_management.GetCatalogInfoInput,
        catalog_management.get_catalog_info,
        writes=False,
    ),
    _tool(
        "request_catalog_access",
        catalog_management.RequestCatalogAccessInput,
        catalog_management.request_catalog_access,
        writes=True,
    ),
    _tool(
        "request_folder_access",
        catalog_management.RequestFolderAccessInput,
        catalog_management.request_folder_access,
        writes=True,
    ),
    _tool(
        "create_collection_set",
        catalog_management.CreateCollectionSetInput,
        catalog_management.create_collection_set,
        writes=True,
    ),
    _tool(
        "create_collection",
        catalog_management.CreateCollectionInput,
        catalog_management.create_collection,
        writes=True,
    ),
    _tool(
        "add_to_collection",
        catalog_management.AddToCollectionInput,
        catalog_management.add_to_collection,
        writes=True,
    ),
    _tool(
        "remove_from_collection",
        catalog_management.RemoveFromCollectionInput,
        catalog_management.remove_from_collection,
        writes=True,
    ),
    _tool(
        "get_collection_photos",
        catalog_management.GetCollectionPhotosInput,
        catalog_management.get_collection_photos,
        writes=False,
    ),
    _tool(
        "create_keyword",
        catalog_management.CreateKeywordInput,
        catalog_management.create_keyword,
        writes=True,
    ),
    _tool(
        "undo_collection_change",
        catalog_management.UndoCollectionChangeInput,
        catalog_management.undo_collection_change,
        writes=True,
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
        "measure_region",
        region_tools.MeasureRegionInput,
        region_tools.measure_region,
        writes=False,
    ),
    _tool(
        "plan_crop",
        composition_tools.PlanCropInput,
        composition_tools.plan_crop,
        writes=False,
    ),
    _tool(
        "view_region",
        composition_tools.ViewRegionInput,
        composition_tools.view_region,
        writes=False,
    ),
    _tool(
        "zone_map",
        scene_tools.ZoneMapInput,
        scene_tools.zone_map,
        writes=False,
    ),
    _tool(
        "find_candidates",
        scene_tools.FindCandidatesInput,
        scene_tools.find_candidates,
        writes=False,
    ),
    _tool(
        "measure_tilt",
        tilt_tools.MeasureTiltInput,
        tilt_tools.measure_tilt,
        writes=False,
    ),
    _tool(
        "auto_correct",
        auto_tools.AutoCorrectInput,
        auto_tools.auto_correct,
        writes=True,
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
        "get_tone_curve",
        tone_curve.GetToneCurveInput,
        tone_curve.get_tone_curve,
        writes=False,
    ),
    _tool(
        "adjust_tone_curve",
        tone_curve.AdjustToneCurveInput,
        tone_curve.adjust_tone_curve,
        writes=True,
    ),
    _tool(
        "replace_background",
        background.ReplaceBackgroundInput,
        background.replace_background,
        writes=True,
    ),
    _tool(
        "remove_objects",
        retouch_tools.RemoveObjectsInput,
        retouch_tools.remove_objects,
        writes=True,
    ),
    _tool(
        "fix_red_eye",
        retouch_tools.FixRedEyeInput,
        retouch_tools.fix_red_eye,
        writes=True,
    ),
    _tool(
        "export_for_delivery",
        delivery_tools.ExportForDeliveryInput,
        delivery_tools.export_for_delivery,
        writes=True,
    ),
    _tool(
        "list_snapshots",
        action_tools.ListSnapshotsInput,
        action_tools.list_snapshots,
        writes=False,
    ),
    _tool(
        "get_retouch_state",
        action_tools.GetRetouchStateInput,
        action_tools.get_retouch_state,
        writes=False,
    ),
    _tool(
        "get_ai_panel_state",
        action_tools.GetAiPanelStateInput,
        action_tools.get_ai_panel_state,
        writes=False,
    ),
    _tool(
        "list_keywords",
        action_tools.ListKeywordsInput,
        action_tools.list_keywords,
        writes=False,
    ),
    _tool(
        "create_snapshot",
        action_tools.CreateSnapshotInput,
        action_tools.create_snapshot,
        writes=True,
    ),
    _tool(
        "restore_snapshot",
        action_tools.RestoreSnapshotInput,
        action_tools.restore_snapshot,
        writes=True,
    ),
    _tool(
        "reset_develop",
        action_tools.ResetDevelopInput,
        action_tools.reset_develop,
        writes=True,
    ),
    _tool(
        "remove_distracting_people",
        action_tools.RemoveDistractingPeopleInput,
        action_tools.remove_distracting_people,
        writes=True,
    ),
    _tool(
        "rotate_photo",
        action_tools.RotatePhotoInput,
        action_tools.rotate_photo,
        writes=True,
    ),
    _tool(
        "set_photo_metadata",
        action_tools.SetPhotoMetadataInput,
        action_tools.set_photo_metadata,
        writes=True,
    ),
    _tool(
        "copy_develop_settings",
        sync_tools.CopyDevelopSettingsInput,
        sync_tools.copy_develop_settings,
        writes=True,
    ),
    _tool(
        "find_photos",
        catalog_search_tools.FindPhotosInput,
        catalog_search_tools.find_photos,
        writes=False,
    ),
    _tool(
        "create_smart_collection",
        catalog_search_tools.CreateSmartCollectionInput,
        catalog_search_tools.create_smart_collection,
        writes=True,
    ),
    _tool(
        "build_smart_previews",
        catalog_search_tools.BuildSmartPreviewsInput,
        catalog_search_tools.build_smart_previews,
        writes=True,
    ),
    _tool(
        "list_catalog_presets",
        catalog_search_tools.ListPresetsInput,
        catalog_search_tools.list_presets,
        writes=False,
    ),
    _tool(
        "apply_metadata_preset",
        catalog_search_tools.ApplyMetadataPresetInput,
        catalog_search_tools.apply_metadata_preset,
        writes=True,
    ),
    _tool(
        "enhance",
        catalog_search_tools.EnhanceInput,
        catalog_search_tools.enhance,
        writes=True,
    ),
    _tool(
        "reflection_removal",
        catalog_search_tools.ReflectionRemovalInput,
        catalog_search_tools.reflection_removal,
        writes=True,
    ),
    _tool("undo", lightroom_tools.UndoInput, lightroom_tools.undo, writes=True),
    _tool(
        "get_library_state",
        library_tools.GetLibraryStateInput,
        library_tools.get_library_state,
        writes=False,
    ),
    _tool(
        "set_library_state",
        library_tools.SetLibraryStateInput,
        library_tools.set_library_state,
        writes=True,
    ),
    _tool(
        "undo_library_change",
        library_tools.UndoLibraryChangeInput,
        library_tools.undo_library_change,
        writes=True,
    ),
    _tool(
        "batch_set_library_state",
        batch.BatchSetLibraryStateInput,
        batch.batch_set_library_state,
        writes=True,
    ),
    _tool(
        "batch_set_develop_parameters",
        batch.BatchSetDevelopParametersInput,
        batch.batch_set_develop_parameters,
        writes=True,
    ),
    _tool("undo_batch", batch.UndoBatchInput, batch.undo_batch, writes=True),
    _tool(
        "list_presets",
        lightroom_tools.ListPresetsInput,
        lightroom_tools.list_presets,
        writes=False,
    ),
    _tool(
        "apply_preset", lightroom_tools.ApplyPresetInput, lightroom_tools.apply_preset, writes=True
    ),
    _tool("get_preset", preset_tools.GetPresetInput, preset_tools.get_preset, writes=False),
    _tool("try_presets", preset_tools.TryPresetsInput, preset_tools.try_presets, writes=True),
    _tool(
        "get_preset_notes",
        preset_tools.GetPresetNotesInput,
        preset_tools.get_preset_notes,
        writes=False,
    ),
    _tool(
        "save_preset_note",
        preset_tools.SavePresetNoteInput,
        preset_tools.save_preset_note,
        writes=False,
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
