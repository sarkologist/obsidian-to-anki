from __future__ import annotations

import atexit
import json
import os
import secrets
import threading
import traceback
import weakref
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import aqt
from aqt import gui_hooks
from aqt.qt import QApplication

BRIDGE_FILENAME = "alfred-anki-bridge.json"
MAX_BODY_BYTES = 5 * 1024 * 1024

_editors: weakref.WeakSet[Any] = weakref.WeakSet()
_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_token = secrets.token_urlsafe(32)


def _bridge_file_path() -> str:
    try:
        return os.path.join(aqt.mw.pm.base, BRIDGE_FILENAME)
    except Exception:
        return os.path.join(
            os.path.expanduser("~"),
            "Library",
            "Application Support",
            "Anki2",
            BRIDGE_FILENAME,
        )


def _write_bridge_file() -> None:
    if not _server:
        return

    data = {
        "url": f"http://127.0.0.1:{_server.server_port}/insert",
        "token": _token,
        "pid": os.getpid(),
    }
    path = _bridge_file_path()
    tmp_path = f"{path}.tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp_path, "w", encoding="utf8") as file:
        json.dump(data, file)
    os.replace(tmp_path, path)


def _remove_bridge_file() -> None:
    try:
        os.unlink(_bridge_file_path())
    except FileNotFoundError:
        pass
    except Exception:
        traceback.print_exc()


def _remember_editor(editor: Any) -> None:
    _editors.add(editor)


def _is_focus_inside(editor: Any) -> bool:
    focus = QApplication.focusWidget()
    if not focus or not getattr(editor, "web", None):
        return False

    widget = editor.web
    while focus:
        if focus is widget:
            return True
        focus = focus.parentWidget()

    return False


def _editor_window(editor: Any) -> Any:
    return getattr(editor, "parentWindow", None) or editor.widget.window()


def _editor_score(editor: Any) -> tuple[int, int, int, int, int]:
    if not getattr(editor, "note", None) or not getattr(editor, "web", None):
        return (-1, -1, -1, -1, -1)

    window = _editor_window(editor)
    return (
        int(getattr(editor, "currentField", None) is not None),
        int(_is_focus_inside(editor)),
        int(bool(window and window.isActiveWindow())),
        int(editor.web.isVisible()),
        int(bool(window and window.isVisible())),
    )


def _active_editor() -> Any | None:
    candidates = [
        editor
        for editor in list(_editors)
        if getattr(editor, "note", None) and getattr(editor, "web", None)
    ]
    if not candidates:
        return None

    candidates.sort(key=_editor_score, reverse=True)
    best = candidates[0]
    if _editor_score(best)[0] <= 0:
        return None
    return best


def _insert_html_on_main(html: str) -> dict[str, Any]:
    editor = _active_editor()
    if not editor:
        return {
            "ok": False,
            "error": "No active Anki editor field is focused.",
        }

    editor.doPaste(html, internal=False, extended=True)
    return {
        "ok": True,
        "field": editor.currentField,
        "mode": getattr(editor.editorMode, "name", str(editor.editorMode)),
    }


def _run_on_main_sync(func: Any, timeout: float = 5.0) -> dict[str, Any]:
    done = threading.Event()
    result: dict[str, Any] = {}

    def wrapped() -> None:
        nonlocal result
        try:
            result = func()
        except Exception:
            result = {
                "ok": False,
                "error": traceback.format_exc(),
            }
        finally:
            done.set()

    aqt.mw.taskman.run_on_main(wrapped)
    if not done.wait(timeout):
        return {
            "ok": False,
            "error": "Timed out waiting for Anki's main thread.",
        }

    return result


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "AlfredAnkiBridge/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        parsed = urlparse(self.path)
        query_token = parse_qs(parsed.query).get("token", [""])[0]
        bearer = self.headers.get("Authorization", "")
        header_token = bearer.removeprefix("Bearer ").strip()
        return query_token == _token or header_token == _token

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/health" or not self._authorized():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False})
            return

        self._send_json(HTTPStatus.OK, {"ok": True})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/insert":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False})
            return

        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False})
            return

        if not self._authorized():
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Invalid Content-Length header."},
            )
            return

        if content_length <= 0 or content_length > MAX_BODY_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "Invalid request body size."},
            )
            return

        html = self.rfile.read(content_length).decode("utf8")
        result = _run_on_main_sync(lambda: _insert_html_on_main(html))
        status = HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT
        self._send_json(status, result)


def _start_server() -> None:
    global _server, _server_thread

    if _server:
        _write_bridge_file()
        return

    _server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
    _server_thread = threading.Thread(
        target=_server.serve_forever,
        name="AlfredAnkiBridge",
        daemon=True,
    )
    _server_thread.start()
    _write_bridge_file()


def _shutdown_server() -> None:
    global _server

    _remove_bridge_file()
    if _server:
        _server.shutdown()
        _server.server_close()
        _server = None


gui_hooks.editor_did_init.append(_remember_editor)
gui_hooks.profile_did_open.append(_start_server)
gui_hooks.profile_will_close.append(_remove_bridge_file)
atexit.register(_shutdown_server)
