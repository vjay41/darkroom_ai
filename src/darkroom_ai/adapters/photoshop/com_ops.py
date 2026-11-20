"""Photoshop COM operations. These run INSIDE the worker child process (see worker.py).

This is the only module that talks COM to Photoshop. It attaches to a running Photoshop; when
auto_start is on, COM launches Photoshop if it is not running. Every operation returns plain
JSON-like data so it can cross the process boundary.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from darkroom_ai.errors import DarkroomError, ErrorCode

PS_WINDOW_CLASS = "Photoshop"
PS_DISPLAY_NO_DIALOGS = 3  # PsDialogModes.psDisplayNoDialogs
PS_DO_NOT_SAVE_CHANGES = 2  # PsSaveOptions.psDoNotSaveChanges
# COM "server is busy" results: Photoshop answers like this while a modal dialog is open.
RPC_E_CALL_REJECTED = -2147418111  # 0x80010001
RPC_E_SERVERCALL_RETRYLATER = -2147417846  # 0x8001010A
BUSY_HRESULTS = {RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER}


def _photoshop_window_exists() -> bool:
    import pywintypes
    import win32gui

    try:
        return bool(win32gui.FindWindow(PS_WINDOW_CLASS, None))
    except pywintypes.error:
        return False


def _attach(prog_id: str, auto_start: bool) -> Any:
    """Connect to Photoshop. With auto_start, COM launches it when it is not running."""
    import win32com.client

    was_running = _photoshop_window_exists()
    if not was_running and not auto_start:
        raise DarkroomError(
            ErrorCode.PS_NOT_REACHABLE,
            "Photoshop is not running.",
            next_step="Start Photoshop, get past any start or sign-in screen, close any open "
            "dialog, then retry (or set photoshop.auto_start = true).",
            details={"reason": "not_running"},
        )
    app = win32com.client.Dispatch(prog_id)
    if not was_running:
        app.Visible = True
    return app


@contextmanager
def _no_dialogs(app: Any) -> Iterator[None]:
    """Suppress Photoshop's own dialogs for our calls and restore the user's setting."""
    previous = app.DisplayDialogs
    app.DisplayDialogs = PS_DISPLAY_NO_DIALOGS
    try:
        yield
    finally:
        app.DisplayDialogs = previous


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _find_document(app: Any, path: str) -> Any:
    import pywintypes

    for index in range(1, app.Documents.Count + 1):
        document = app.Documents.Item(index)
        try:
            full_name = str(document.FullName)
        except pywintypes.com_error:
            continue  # a document that was never saved has no path
        if _same_path(full_name, path):
            return document
    raise DarkroomError(
        ErrorCode.PS_COMMAND_FAILED,
        f"The document is not open in Photoshop: {path}",
        details={"reason": "document_not_open", "path": path},
    )


# -- operations (name -> function(app, **kwargs)) -------------------------------------------------


def op_info(app: Any) -> dict[str, Any]:
    return {
        "name": str(app.Name),
        "version": str(app.Version),
        "documents": int(app.Documents.Count),
    }


def op_open_document(app: Any, *, path: str) -> dict[str, Any]:
    with _no_dialogs(app):
        document = app.Open(path)
        return {"name": str(document.Name)}


def op_run_script(app: Any, *, code: str, path: str | None = None) -> dict[str, Any]:
    """Run ExtendScript. With `path`, that document is made active first."""
    with _no_dialogs(app):
        if path is not None:
            app.ActiveDocument = _find_document(app, path)
        return {"result": str(app.DoJavaScript(code))}


def op_add_layer(app: Any, *, path: str, name: str) -> dict[str, Any]:
    with _no_dialogs(app):
        document = _find_document(app, path)
        layer = document.ArtLayers.Add()
        layer.Name = name
        return {"layers": int(document.Layers.Count), "layer_name": str(layer.Name)}


def op_save_psd(app: Any, *, path: str, out_path: str) -> dict[str, Any]:
    """Save a layered PSD copy of the open document to a NEW file (never overwrites)."""
    import win32com.client

    if os.path.exists(out_path):
        raise DarkroomError(
            ErrorCode.PS_COMMAND_FAILED,
            f"Refusing to overwrite an existing file: {out_path}",
            details={"reason": "exists", "path": out_path},
        )
    with _no_dialogs(app):
        document = _find_document(app, path)
        options = win32com.client.Dispatch("Photoshop.PhotoshopSaveOptions")
        options.Layers = True
        options.EmbedColorProfile = True
        document.SaveAs(out_path, options, True)  # True = save as a copy
    return {"path": out_path, "size": os.path.getsize(out_path)}


def op_close_document(app: Any, *, path: str) -> dict[str, Any]:
    """Close the document WITHOUT saving."""
    with _no_dialogs(app):
        _find_document(app, path).Close(PS_DO_NOT_SAVE_CHANGES)
    return {"closed": True}


OPERATIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "info": op_info,
    "open_document": op_open_document,
    "run_script": op_run_script,
    "add_layer": op_add_layer,
    "save_psd": op_save_psd,
    "close_document": op_close_document,
}


def run_operation(
    prog_id: str, name: str, kwargs: dict[str, Any], *, auto_start: bool = False
) -> dict[str, Any]:
    """Run one operation against Photoshop. Raises DarkroomError on any failure."""
    if name == "diag_sleep":  # used by tests to prove the deadline; does not touch Photoshop
        time.sleep(float(kwargs["seconds"]))
        return {"slept": kwargs["seconds"]}

    import pythoncom
    import pywintypes

    operation = OPERATIONS.get(name)
    if operation is None:
        raise DarkroomError(ErrorCode.PS_COMMAND_FAILED, f"Unknown Photoshop operation: {name}")

    pythoncom.CoInitialize()
    try:
        app = _attach(prog_id, auto_start)
        return operation(app, **kwargs)
    except pywintypes.com_error as exc:
        hresult = int(exc.hresult)
        scode = int(exc.excepinfo[5]) if exc.excepinfo and exc.excepinfo[5] else 0
        text = str(exc.excepinfo[2]).strip() if exc.excepinfo and exc.excepinfo[2] else str(exc)
        if hresult in BUSY_HRESULTS or scode in BUSY_HRESULTS:
            raise DarkroomError(
                ErrorCode.PS_NOT_REACHABLE,
                "Photoshop is busy and rejected the call (likely a dialog is open).",
                details={"reason": "busy", "hresult": hresult, "operation": name},
            ) from exc
        raise DarkroomError(
            ErrorCode.PS_COMMAND_FAILED,
            f"Photoshop could not run '{name}': {text}",
            details={"hresult": hresult, "scode": scode, "operation": name},
        ) from exc
    finally:
        pythoncom.CoUninitialize()
