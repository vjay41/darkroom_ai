"""Is Lightroom / Photoshop running and ready? Start them when asked.

`get_app_status` only looks (it never starts or changes anything). `ensure_apps_running` starts a
program that is not running, using the install path from the settings, and waits for it to answer.
A program that is running but does not answer is NOT restarted: the person is told what to clear
(a dialog, a sign-in), because closing an app could lose their work.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from darkroom_ai.errors import DarkroomError
from darkroom_ai.tools.context import Outcome, ToolContext, ToolResult

_sleep = time.sleep
POLL_S = 3.0

AppName = Literal["lightroom", "photoshop"]
State = Literal["ready", "busy", "not_running", "needs_user", "not_installed", "unknown"]


class AppState(BaseModel):
    model_config = ConfigDict(frozen=True)

    app: str
    state: State
    detail: str = ""
    next_step: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)


def _lightroom(ctx: ToolContext) -> AppState:
    apps = ctx.settings.apps
    running = ctx.processes.is_running(apps.lightroom_process) if ctx.processes else None
    if running is False:
        return AppState(
            app="lightroom",
            state="not_running",
            detail="Lightroom Classic is not running.",
            next_step="Call ensure_apps_running with app 'lightroom'.",
        )
    # The bridge answers this at once even when a command is stuck, so ask it first.
    try:
        health = ctx.lightroom.bridge_status()
    except DarkroomError:
        health = None
    if health is not None and health.busy:
        running_s = float(health.busy.get("running_s", 0) or 0)
        budget_s = float(health.busy.get("budget_s", 0) or 0)
        over = bool(health.busy.get("over_budget"))
        if over or running_s > 5:
            return AppState(
                app="lightroom",
                state="busy",
                detail=(
                    f"Lightroom's bridge is running '{health.busy.get('command')}' "
                    f"({running_s:.0f} s of {budget_s:.0f} s allowed"
                    + ("; it is over its limit, so it is probably stuck" if over else "")
                    + ")."
                ),
                next_step=(
                    "Look at Lightroom for an open dialog or a spinning progress bar and clear it."
                    if over
                    else "Wait a few seconds and call get_app_status again."
                ),
                facts={"waiting_requests": health.waiting, "plugin_version": health.plugin_version},
            )
    try:
        info = ctx.lightroom.ping()
    except DarkroomError as exc:
        return AppState(
            app="lightroom",
            state="needs_user" if running else "unknown",
            detail=f"[{exc.code.value}] {exc.message}",
            next_step=exc.next_step,
        )
    return AppState(
        app="lightroom",
        state="ready",
        detail="Lightroom Classic answers on the darkroom bridge.",
        facts={
            "lightroom_version": info.lightroom_version,
            "plugin_version": info.plugin_version,
            "open_catalog": info.catalog_path,
            "edits_allowed_in_this_catalog": info.writes_allowed,
            **(
                {
                    "bridge_requests_handled": health.handled,
                    "bridge_requests_failed": health.failed,
                    "bridge_last_error": health.last_error,
                }
                if health is not None
                else {}
            ),
        },
        next_step=(
            ""
            if info.writes_allowed
            else "Reading works. Edits are refused: the open catalog is not the allowed one."
        ),
    )


def _photoshop(ctx: ToolContext) -> AppState:
    apps = ctx.settings.apps
    running = ctx.processes.is_running(apps.photoshop_process) if ctx.processes else None
    if running is False:
        return AppState(
            app="photoshop",
            state="not_running",
            detail="Photoshop is not running.",
            next_step="Call ensure_apps_running with app 'photoshop'.",
        )
    if ctx.photoshop is None:
        return AppState(app="photoshop", state="unknown", detail="Photoshop is not set up here.")
    try:
        info = ctx.photoshop.info()
    except DarkroomError as exc:
        return AppState(
            app="photoshop",
            state="needs_user" if running else "unknown",
            detail=f"[{exc.code.value}] {exc.message}",
            next_step=exc.next_step,
        )
    return AppState(
        app="photoshop",
        state="ready",
        detail="Photoshop answers.",
        facts={"name": info.name, "version": info.version, "open_documents": info.documents},
    )


def _check(ctx: ToolContext, app: AppName) -> AppState:
    return _lightroom(ctx) if app == "lightroom" else _photoshop(ctx)


class GetAppStatusInput(BaseModel):
    """Check whether Lightroom Classic and Photoshop are running and ready to be used. Reads only:
    it never starts or closes anything. States: ready; busy (a command runs or is stuck in
    Lightroom; the detail says how long); not_running; needs_user (running but not answering, e.g.
    a dialog is open; the next step says what to clear); not_installed; unknown."""

    model_config = ConfigDict(extra="forbid")


class AppStatusResult(ToolResult):
    lightroom: AppState
    photoshop: AppState
    all_ready: bool


def get_app_status(ctx: ToolContext, args: GetAppStatusInput) -> Outcome:
    lightroom, photoshop = _check(ctx, "lightroom"), _check(ctx, "photoshop")
    ready = lightroom.state == "ready" and photoshop.state == "ready"
    return Outcome(AppStatusResult(lightroom=lightroom, photoshop=photoshop, all_ready=ready))


class EnsureAppsRunningInput(BaseModel):
    """Start Lightroom Classic or Photoshop if it is not running, and wait until it answers. An app
    that is already running is never restarted. If it is running but not answering, you get state
    needs_user: tell the person what to clear, then call get_app_status until it says ready."""

    model_config = ConfigDict(extra="forbid")

    app: AppName


class EnsureAppsRunningResult(ToolResult):
    status: AppState
    started: bool
    waited_s: float


def ensure_apps_running(ctx: ToolContext, args: EnsureAppsRunningInput) -> Outcome:
    apps = ctx.settings.apps
    status = _check(ctx, args.app)
    if status.state != "not_running" or ctx.processes is None:
        return Outcome(EnsureAppsRunningResult(status=status, started=False, waited_s=0.0))
    exe = Path(apps.lightroom_exe if args.app == "lightroom" else apps.photoshop_exe)
    if not exe.is_file():
        missing = AppState(
            app=args.app,
            state="not_installed",
            detail=f"The program file was not found: {exe}",
            next_step="Set the correct path under [apps] in darkroom.local.toml.",
        )
        return Outcome(EnsureAppsRunningResult(status=missing, started=False, waited_s=0.0))
    arguments: list[str] = []
    catalog = ctx.settings.lightroom.test_catalog
    if args.app == "lightroom" and catalog is not None:
        arguments.append(str(catalog))  # opens the allowed catalog, not whatever was last open
    ctx.processes.start(exe, arguments)
    started_at = time.monotonic()
    waited = 0.0
    while waited < apps.start_wait_s:
        _sleep(POLL_S)
        waited = round(time.monotonic() - started_at, 1)
        status = _check(ctx, args.app)
        if status.state == "ready":
            break
    if status.state != "ready":
        status = status.model_copy(
            update={
                "next_step": status.next_step
                or "It was started but is not answering yet. Wait, then call get_app_status."
            }
        )
    return Outcome(EnsureAppsRunningResult(status=status, started=True, waited_s=waited))
