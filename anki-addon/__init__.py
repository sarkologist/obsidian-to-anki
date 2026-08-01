from __future__ import annotations

import atexit
import hashlib
import json
import os
import secrets
import threading
import traceback
import weakref
from html import escape as html_escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import aqt
from aqt import gui_hooks
from aqt.qt import QApplication

BRIDGE_FILENAME = "alfred-anki-bridge.json"
MAX_BODY_BYTES = 5 * 1024 * 1024
# Images can be larger than the HTML payload; allow more headroom for /media uploads.
MAX_MEDIA_BYTES = 32 * 1024 * 1024

# Minimal magic-number sniffing, used only when the uploaded name lacks a usable extension.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)

# M0 (background-field insert): when the insert request arrives, Anki is no longer the
# frontmost app (the user triggered from Obsidian), so no editor field is focused. We
# remember the last field the user was in and insert there. Set BRIDGE_RAISE_ON_INSERT=1
# to also raise/activate the Anki window as a fallback if the gentle focus path fails to
# land the paste — useful for diagnosing what your setup needs.
RAISE_ON_INSERT = os.environ.get("BRIDGE_RAISE_ON_INSERT") == "1"

# Caret-within-field insert: the background path re-focuses the target field with Anki's
# focusField(), whose refocus() calls moveCaretToEnd() — so without help every paste lands
# at the *end* of the field. This script (injected into the editor webview) records the
# caret position while the user edits and can freeze/restore it around focusField(), so the
# paste lands where the caret actually was. It leans on Anki's own selection serializer
# (require("anki/location").saveSelection/restoreSelection), which encodes a caret as
# coordinates relative to the field's contenteditable — stable across the blur/focus dance.
# Everything is best-effort and guarded: if the location package is missing (older Anki) or
# the coordinates no longer resolve, the helpers return false and we fall back to Anki's
# end-of-field behaviour. Idempotent: re-injecting is a no-op after the first run.
#
# The same script also leaves the caret *after* the inserted content. Anki's paste is
# execCommand("insertHTML") followed by DOM surgery (decorating MathJax, unwrapping headings
# the paste wrapped around blocks); when the surgery re-creates the last inserted node the
# browser's post-insert caret dies with it and collapses back to the insertion point, so the
# caret ends up in front of the paste — type, and what you type lands before what you just
# sent over. We bookmark the end of the paste target before pasting and move the caret onto
# the bookmark afterwards, which survives that surgery.
_CARET_JS = r"""
(function () {
  if (window.__otaCaretHook) { return; }
  window.__otaCaretHook = true;

  var MARKER_ATTR = "data-ota-insert-marker";
  var MARKER_SELECTOR = "[" + MARKER_ATTR + "]";

  // The focused rich-text editable, mirroring Anki's activeRichTextEditable(): it is
  // either document.activeElement itself or one shadow level down (the RichTextInput host).
  function editable() {
    var a = document.activeElement;
    if (!a) { return null; }
    if (a.matches && a.matches("anki-editable")) { return a; }
    var s = a.shadowRoot && a.shadowRoot.activeElement;
    if (s && s.matches && s.matches("anki-editable")) { return s; }
    return null;
  }
  window.__otaEditable = editable;

  function loc() {
    try { return require("anki/location"); } catch (e) { return null; }
  }

  // Continuously remember the caret while a field is focused, so we still have a target
  // even if the live selection is disturbed before we freeze it.
  document.addEventListener("selectionchange", function () {
    var ed = editable();
    if (!ed) { return; }
    var l = loc();
    if (!l) { return; }
    try {
      var saved = l.saveSelection(ed);
      if (saved) { window.__otaCaret = saved; window.__otaCaretEditable = ed; }
    } catch (e) {}
  }, true);

  // Snapshot the caret. Prefer the live selection (the target field is still the active
  // editable while Anki is backgrounded); fall back to the last position the recorder saw.
  // Returns true if we captured something to restore.
  //
  // The caller must invoke this *before* touching the editor at all. Anything that focuses a
  // field — loadNote(focusTo=...) as well as focusField() — ends in moveCaretToEnd(), which
  // both moves the live caret and (via selectionchange) overwrites the recorder's copy. Once
  // that has happened the user's position is gone from every source we have, and freezing
  // then silently captures the end of the field instead.
  window.__otaFreezeCaret = function () {
    var ed = editable();
    var saved = null;
    var l = loc();
    if (ed && l) {
      try { saved = l.saveSelection(ed); } catch (e) {}
    }
    if (!saved) { saved = window.__otaCaret || null; ed = window.__otaCaretEditable || ed; }
    window.__otaFrozenCaret = saved;
    window.__otaFrozenEditable = saved ? ed : null;
    return !!saved;
  };

  // Restore the frozen caret into the now-active target field, just before pasting.
  window.__otaRestoreCaret = function () {
    var saved = window.__otaFrozenCaret;
    if (!saved) { return false; }
    var ed = editable();
    if (!ed) { return false; }
    // Refuse only when the field we froze is still on the page but is not the one now
    // focused: focusField() landed elsewhere, and restoring would corrupt another field. A
    // frozen element that is merely *detached* is fine — the editor re-rendered its fields
    // (loadNote does this) and handed the target a fresh element. The coordinates are
    // relative to the field, so they still resolve against the replacement.
    var frozen = window.__otaFrozenEditable;
    if (frozen && frozen !== ed && frozen.isConnected) { return false; }
    var l = loc();
    if (!l) { return false; }
    try { l.restoreSelection(ed, saved); return true; } catch (e) { return false; }
  };

  function selectionFor(ed) {
    var root = ed.getRootNode();
    return root.getSelection ? root.getSelection() : document.getSelection();
  }

  function dropMarkers(ed) {
    var stale = ed.querySelectorAll(MARKER_SELECTOR);
    for (var i = 0; i < stale.length; i++) { stale[i].remove(); }
  }

  // Bookmark where the pasted content will end, by parking a node at the end of the paste
  // target and pointing the selection just before it: execCommand("insertHTML") then inserts
  // in front of the bookmark, so afterwards the bookmark sits exactly where the caret
  // belongs.
  //
  // The bookmark is a src-less <img> on purpose. It renders nothing (no source, no broken
  // icon, zero size) but is a replaced element, so it occupies a caret position of its own —
  // an empty <span> doesn't, and Chromium normalises the caret straight past it and inserts
  // on the far side. It also survives the block re-shuffling a multi-block paste triggers,
  // where a marker text node gets merged away.
  //
  // block="true" is Anki's own "treat this as a block element" opt-in (elementIsBlock). Anki
  // cleans up a heading the paste wrapped around blocks, and that check bails on any
  // non-block child: an unmarked bookmark inside the heading suppresses the cleanup, leaving
  // an empty <h1> behind once the bookmark is removed. Nothing styles the attribute, so the
  // bookmark stays inline for caret purposes.
  window.__otaMarkInsertEnd = function () {
    var ed = editable();
    if (!ed) { return false; }
    var sel = selectionFor(ed);
    if (!sel || sel.rangeCount === 0) { return false; }
    var range = sel.getRangeAt(0);
    if (!ed.contains(range.commonAncestorContainer)) { return false; }
    dropMarkers(ed);
    var marker = document.createElement("img");
    marker.setAttribute(MARKER_ATTR, "1");
    marker.setAttribute("block", "true");
    var collapsed = range.collapsed;
    var end = range.cloneRange();
    end.collapse(false);
    try {
      end.insertNode(marker);
      // Keep the original target — including a non-collapsed selection, which the paste is
      // supposed to replace — now bounded by the bookmark. A plain caret must stay
      // *collapsed*: insertNode splits the text node around it, and a range spanning that
      // (empty) split reaches execCommand as a selection to replace, which throws its own
      // caret handling off.
      if (collapsed) {
        range.setStartBefore(marker);
        range.collapse(true);
      } else {
        range.setEndBefore(marker);
      }
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (e) {
      marker.remove();
      return false;
    }
    window.__otaMarkerEditable = ed;
    return true;
  };

  // Put the caret where the bookmark sits (i.e. after the inserted content) and remove it.
  // Always call this after a marked paste, even if the paste failed, so no bookmark is left
  // behind in the field.
  window.__otaCaretAfterInsert = function () {
    var ed = window.__otaMarkerEditable;
    window.__otaMarkerEditable = null;
    if (!ed) { return false; }
    var marker = ed.querySelector(MARKER_SELECTOR);
    if (!marker) { return false; }
    var parent = marker.parentNode;
    var index = Array.prototype.indexOf.call(parent.childNodes, marker);
    dropMarkers(ed);
    var ok = false;
    try {
      var range = document.createRange();
      range.setStart(parent, index);
      range.collapse(true);
      var sel = selectionFor(ed);
      if (sel) {
        sel.removeAllRanges();
        sel.addRange(range);
        ok = true;
      }
    } catch (e) {}
    // The editor's field save is debounced and captures the HTML as it was mid-paste, which
    // included the bookmark. Nudge it to re-read the now-clean field so the bookmark can't
    // be written to the note.
    try {
      ed.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true }));
    } catch (e) {}
    return ok;
  };
})();
"""


def _inject_caret_helpers(editor: Any) -> None:
    """Install the caret recorder/restore helpers into the editor webview (idempotent).

    Injected on note load so the recorder is listening while the user edits, and again just
    before a background insert as a safety net if the load-time injection was missed."""
    web = getattr(editor, "web", None)
    if web is None:
        return
    try:
        web.eval(_CARET_JS)
    except Exception:
        traceback.print_exc()

_editors: weakref.WeakSet[Any] = weakref.WeakSet()
_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_token = secrets.token_urlsafe(32)

# Last editor + field index observed focused, so we can target it once focus has moved
# away to another app. Stored as a weakref so a closed editor can be garbage collected.
# We also record a note key at capture time: if the editor has since loaded a different
# note, the remembered field index is meaningless and the memory must be discarded. The
# key is (id, guid) — unsaved Add-window notes all share id 0, so guid is what actually
# distinguishes them, while id covers notes without a guid.
_last_focus_ref: "weakref.ref[Any] | None" = None
_last_focus_field: int | None = None
_last_focus_note_key: tuple[Any, Any, Any] | None = None


def _note_key(note: Any) -> tuple[Any, Any, Any] | None:
    if note is None:
        return None
    # Include mid (notetype id): converting a note to another notetype keeps the same
    # id/guid but changes the field layout, which would otherwise make a stale field
    # index look valid.
    return (getattr(note, "id", None), getattr(note, "guid", None), getattr(note, "mid", None))


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


def _remember_focus(editor: Any, field: int | None = None) -> None:
    """Record the editor + field the user is in, so we can target it once focus has
    moved to another app (e.g. Obsidian) and no field is live-focused anymore. Pass an
    explicit `field` when capturing from a blur/unfocus event, where `currentField` may
    already have been cleared."""
    global _last_focus_ref, _last_focus_field, _last_focus_note_key
    if field is None:
        field = getattr(editor, "currentField", None)
    if field is None:
        return
    note = getattr(editor, "note", None)
    _last_focus_ref = weakref.ref(editor)
    _last_focus_field = field
    _last_focus_note_key = _note_key(note)


def _clear_focus_memory() -> None:
    global _last_focus_ref, _last_focus_field, _last_focus_note_key
    _last_focus_ref = None
    _last_focus_field = None
    _last_focus_note_key = None


def _remembered_editor() -> Any | None:
    if _last_focus_ref is None:
        return None
    editor = _last_focus_ref()
    if not editor or not getattr(editor, "web", None):
        return None
    note = getattr(editor, "note", None)
    if not note:
        return None
    # The editor may have loaded a different note since we remembered the field; if so the
    # remembered index is meaningless, so refuse it rather than paste into the wrong note.
    if _note_key(note) != _last_focus_note_key:
        return None
    return editor


def _field_count(editor: Any) -> int | None:
    fields = getattr(getattr(editor, "note", None), "fields", None)
    try:
        return len(fields) if fields is not None else None
    except TypeError:
        return None


def _on_typing_timer(*args: Any) -> None:
    """gui_hooks.editor_did_fire_typing_timer passes the note; resolve the editor whose
    note it is and remember the field being edited. A backup capture path — the primary
    one is _on_unfocus_field, which fires without waiting for the typing debounce."""
    note = args[0] if args else None
    for editor in list(_editors):
        if (
            getattr(editor, "note", None) is note
            and getattr(editor, "currentField", None) is not None
        ):
            _remember_focus(editor)
            return


def _on_unfocus_field(changed: bool, note: Any = None, field_idx: int | None = None) -> bool:
    """gui_hooks.editor_did_unfocus_field(changed, note, field_idx). Fires the moment a
    field loses focus (including when the user leaves for another app), and carries the
    field index explicitly — so we capture the target even on a fast type-then-switch,
    before `currentField` is cleared.

    This is a *filter* hook: callbacks must return the (possibly updated) `changed` bool.
    We only observe, so we pass it straight through — returning None would clobber other
    add-ons' changes and suppress Anki's follow-up editor reload."""
    if field_idx is not None and note is not None:
        for editor in list(_editors):
            if getattr(editor, "note", None) is note:
                _remember_focus(editor, field_idx)
                break
    return changed


def _on_load_note(editor: Any) -> None:
    """Runs on every note load. Injects the caret helpers (so the recorder is listening
    while the user edits) and drops stale focus memory.

    When an editor loads a *different* note, any field we remembered for it is stale.

    Must compare note ids, not just fire on any load: editing a field with changes blurs
    with `changed=True`, which makes Anki reload the *same* note. That reload fires this
    hook right after we recorded the target, so clearing unconditionally would wipe the
    memory the background /insert flow depends on."""
    _inject_caret_helpers(editor)
    if _last_focus_ref is not None and _last_focus_ref() is editor:
        note = getattr(editor, "note", None)
        if _note_key(note) != _last_focus_note_key:
            _clear_focus_memory()


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
    _remember_focus(best)
    return best


def _source_field_name(note: Any) -> str | None:
    """Name of the note's "source" field (case-insensitive), or None if it has none."""
    try:
        keys = note.keys()
    except Exception:
        return None
    return next((key for key in keys if key.lower() == "source"), None)


def _append_source_field(note: Any, source_url: str) -> bool:
    """If the note has a "source" field, append `source_url` to it as a clickable link on a
    new line. Idempotent: a URL already present is not appended again (so re-sending from the
    same Obsidian note doesn't stack duplicates). Returns True if the field was modified.

    Only mutates the in-memory note; the caller reloads the editor so the change is shown and
    persisted when the note is next flushed."""
    field_name = _source_field_name(note)
    if field_name is None:
        return False
    escaped = html_escape(source_url, quote=True)
    link = f'<a href="{escaped}">{escaped}</a>'
    current = note[field_name]
    # Match the whole anchor, not just the URL substring: a bare `escaped in current` would
    # treat file=Foo as already present when the field holds a link to file=FooBar.
    if link in current:
        return False
    note[field_name] = f"{current}<br>{link}" if current.strip() else link
    return True


def _paste_leaving_caret_after(editor: Any, html: str) -> None:
    """Paste `html`, leaving the caret after the inserted content rather than in front of it.

    Bookmark the end of the paste target first, then hand the caret back to the bookmark once
    the paste — and Anki's post-paste DOM surgery — is done. The clean-up eval runs even if
    doPaste raises, so a failed paste can't strand the bookmark in the field. Both evals are
    guarded on the helper existing: an editor webview still running an older injection just
    keeps Anki's own caret."""
    editor.web.eval("window.__otaMarkInsertEnd && window.__otaMarkInsertEnd();")
    try:
        editor.doPaste(html, internal=False, extended=True)
    finally:
        editor.web.eval("window.__otaCaretAfterInsert && window.__otaCaretAfterInsert();")


def _insert_html_on_main(
    html: str, source_url: str | None, finish: Callable[[dict[str, Any]], None]
) -> None:
    """Paste `html` into the remembered field, at the caret the user left there.

    Completes via `finish` rather than by returning, because the caret restore runs in the
    webview and we want to report what it actually did (see `restored_caret`)."""
    # Prefer a live-focused field; otherwise fall back to the last field we remember the
    # user being in (the common case when triggering from Obsidian: Anki is backgrounded).
    live = _active_editor()
    remembered = None if live else _remembered_editor()
    editor = live or remembered
    if not editor:
        finish(
            {
                "ok": False,
                "error": (
                    "No Anki editor to paste into. Open the Add/Edit window and click a "
                    "field at least once so the bridge knows the target."
                ),
            }
        )
        return

    from_memory = live is None
    field_idx = getattr(editor, "currentField", None)
    if field_idx is None:
        field_idx = _last_focus_field if from_memory else None
    if field_idx is None:
        field_idx = 0

    # If the remembered field no longer exists (e.g. switched to a notetype with fewer
    # fields), fail rather than silently pasting into a different field the user didn't
    # seed — a wrong-field paste that reports success is worse than a clear error.
    count = _field_count(editor)
    if count is not None and field_idx >= count:
        _clear_focus_memory()
        finish(
            {
                "ok": False,
                "error": (
                    f"The field you seeded (index {field_idx}) no longer exists — the note "
                    f"now has {count} field(s). Click the target field in Anki again."
                ),
            }
        )
        return

    was_focused = _is_focus_inside(editor) and getattr(editor, "currentField", None) is not None
    raised_window = False

    # Freeze the caret before touching the editor in any way. Every path below that focuses a
    # field — the source-field loadNote() as well as focusField() — ends in moveCaretToEnd(),
    # which moves the live caret *and* overwrites the recorder's copy of it. Freezing after
    # any of that would capture the end of the field and faithfully restore the wrong spot.
    _inject_caret_helpers(editor)
    editor.web.eval("window.__otaFreezeCaret && window.__otaFreezeCaret();")

    # If asked, drop the Obsidian page URL into the note's "source" field (when it has one).
    # loadNote pushes the change into the webview so it survives the note's next save. It also
    # re-renders every field and parks the caret at the end of `focusTo`, which is why the
    # freeze above has to come first.
    source_updated = False
    note = getattr(editor, "note", None)
    if source_url and note is not None and _append_source_field(note, source_url):
        try:
            editor.loadNote(focusTo=field_idx)
        except Exception:
            traceback.print_exc()
        editor.currentField = field_idx
        source_updated = True

    target_note_key = _note_key(getattr(editor, "note", None))

    def paste_and_finish(restored_caret: Any) -> None:
        # On the blurred path this runs from a webview callback, so the editor has had a
        # chance to move on — clicking another row in the Browse window swaps its note out
        # from under us. Pasting then would dump the clip into a note the user never seeded.
        if _note_key(getattr(editor, "note", None)) != target_note_key:
            finish(
                {
                    "ok": False,
                    "error": (
                        "The editor loaded a different note before the paste landed. "
                        "Nothing was inserted — click the target field again and retry."
                    ),
                }
            )
            return
        finish(
            {
                "ok": True,
                "field": getattr(editor, "currentField", field_idx),
                "target_field_index": field_idx,
                "from_memory": from_memory,
                "was_focused": was_focused,
                "raised_window": raised_window,
                "source_updated": source_updated,
                # True/False as reported by the webview, or None when no restore was needed.
                "restored_caret": restored_caret,
                "mode": getattr(editor, "editorMode", None)
                and getattr(editor.editorMode, "name", str(editor.editorMode)),
            },
            commit=lambda: _paste_leaving_caret_after(editor, html),
        )

    # Already in the field with the caret live, and nothing disturbed it: paste straight in.
    if was_focused and not source_updated:
        paste_and_finish(None)
        return

    # Otherwise the field is blurred (Anki isn't frontmost) or loadNote() just dropped focus.
    # Re-assert it before pasting so the HTML lands in the intended field rather than nowhere.
    window = _editor_window(editor)
    if RAISE_ON_INSERT and window is not None:
        window.activateWindow()
        window.raise_()
        raised_window = True

    try:
        editor.web.setFocus()
    except Exception:
        pass
    editor.currentField = field_idx
    # focusField() places the caret at the *end* of the target field. Evals run in submission
    # order, so the restore below — and the paste it chains — land in this field.
    editor.web.eval(f"focusField({int(field_idx)});")
    # Restore the frozen caret so the paste lands where the user's cursor was, not at the
    # field's end, and paste once the webview tells us how that went. On failure it reports
    # false and focusField()'s end-of-field caret stands, which is the pre-caret behaviour.
    editor.web.evalWithCallback(
        "window.__otaRestoreCaret ? !!window.__otaRestoreCaret() : null",
        paste_and_finish,
    )


def _guess_extension(suggested_name: str, data: bytes) -> str:
    ext = os.path.splitext(suggested_name)[1].lower()
    if ext and len(ext) <= 6:
        return ext
    for magic, guessed in _IMAGE_MAGIC:
        if data.startswith(magic):
            return guessed
    return ".png"


def _store_media_on_main(data: bytes, suggested_name: str) -> dict[str, Any]:
    """Write image bytes into the collection's media folder, named by content hash so the
    same image dedups to one file. Returns the stored filename for the plugin to reference."""
    col = getattr(aqt.mw, "col", None)
    if col is None:
        return {"ok": False, "error": "No Anki collection is open."}

    ext = _guess_extension(suggested_name, data)
    fname = f"ota-{hashlib.sha1(data).hexdigest()[:16]}{ext}"
    try:
        stored = col.media.write_data(fname, data)
    except Exception:
        return {"ok": False, "error": traceback.format_exc()}
    return {"ok": True, "filename": stored}


TIMED_OUT = {"ok": False, "error": "Timed out waiting for Anki's main thread."}


def _run_on_main(func: Any, timeout: float = 5.0) -> dict[str, Any]:
    """Run `func(finish)` on Anki's main thread and block this request thread until it calls
    `finish(result)` — or until we give up.

    `func` may complete asynchronously: /insert finishes from a webview eval callback, so
    that it can report what the caret restore actually did instead of assuming it worked.

    `finish(result, commit=...)` runs `commit` — the paste — only if it wins the race against
    the timeout, and only once. Without that, a callback arriving just after we gave up would
    paste behind the client's back: it has already been told the insert failed, so its retry
    would insert the content a second time."""
    lock = threading.Lock()
    done = threading.Event()
    claimed = False
    result: dict[str, Any] = {}

    def claim() -> bool:
        nonlocal claimed
        with lock:
            if claimed:
                return False
            claimed = True
            return True

    def finish(value: dict[str, Any], commit: Callable[[], None] | None = None) -> None:
        nonlocal result
        if not claim():
            return
        if commit is not None:
            try:
                commit()
            except Exception:
                value = {"ok": False, "error": traceback.format_exc()}
        result = value
        done.set()

    def wrapped() -> None:
        try:
            func(finish)
        except Exception:
            finish({"ok": False, "error": traceback.format_exc()})

    aqt.mw.taskman.run_on_main(wrapped)
    if done.wait(timeout):
        return result

    # Out of time. Claim the completion ourselves so any late callback finds it taken and
    # skips its commit. If we lose that race the callback is already committing, so wait
    # briefly for the result it is about to publish rather than reporting a false failure.
    if claim():
        return dict(TIMED_OUT)
    return result if done.wait(1.0) else dict(TIMED_OUT)


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
        if parsed.path not in ("/insert", "/media"):
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

        limit = MAX_MEDIA_BYTES if parsed.path == "/media" else MAX_BODY_BYTES
        if content_length <= 0 or content_length > limit:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "Invalid request body size."},
            )
            return

        body = self.rfile.read(content_length)
        if parsed.path == "/media":
            name = parse_qs(parsed.query).get("name", [""])[0]
            result = _run_on_main(lambda finish: finish(_store_media_on_main(body, name)))
        else:
            html = body.decode("utf8")
            source_url = parse_qs(parsed.query).get("source_url", [""])[0] or None
            result = _run_on_main(lambda finish: _insert_html_on_main(html, source_url, finish))
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

# Track the field the user is in so we can paste into it once Anki is backgrounded.
# Each hook is guarded because availability varies across Anki versions.
# - editor_did_unfocus_field: primary capture (carries the field index on blur).
# - editor_did_fire_typing_timer: backup capture while typing.
# - editor_did_load_note: drop stale memory when an editor swaps to another note.
for _hook_name, _cb in (
    ("editor_did_unfocus_field", _on_unfocus_field),
    ("editor_did_fire_typing_timer", _on_typing_timer),
    ("editor_did_load_note", _on_load_note),
):
    try:
        getattr(gui_hooks, _hook_name).append(_cb)
    except Exception:
        traceback.print_exc()
