from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class Hook(list):
    pass


class FakeApplication:
    focused = None

    @classmethod
    def focusWidget(cls):
        return cls.focused


def load_addon():
    hooks = types.SimpleNamespace(
        editor_did_init=Hook(),
        profile_did_open=Hook(),
        profile_will_close=Hook(),
        editor_did_unfocus_field=Hook(),
        editor_did_fire_typing_timer=Hook(),
        editor_did_load_note=Hook(),
    )
    aqt = types.ModuleType("aqt")
    aqt.gui_hooks = hooks
    aqt.mw = types.SimpleNamespace(
        pm=types.SimpleNamespace(base="/tmp/obsidian-to-anki-addon-tests")
    )
    qt = types.ModuleType("aqt.qt")
    qt.QApplication = FakeApplication

    path = Path(__file__).parents[1] / "__init__.py"
    spec = importlib.util.spec_from_file_location("obsidian_to_anki_addon_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"aqt": aqt, "aqt.qt": qt}):
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module


class FakeWidget:
    def __init__(self, parent=None):
        self._parent = parent

    def parentWidget(self):
        return self._parent


class FakeWeb(FakeWidget):
    def isVisible(self):
        return True


class FakeWindow:
    def isActiveWindow(self):
        return False

    def isVisible(self):
        return True


class FakeEditor:
    def __init__(self, note_id):
        self.note = types.SimpleNamespace(id=note_id, guid=str(note_id), mid=1)
        self.web = FakeWeb()
        self.currentField = 0
        self.parentWindow = FakeWindow()


class EditorSelectionTest(unittest.TestCase):
    def setUp(self):
        self.addon = load_addon()
        FakeApplication.focused = None

    def tearDown(self):
        self.addon._shutdown_server()

    def test_stale_browser_field_does_not_override_remembered_add_editor(self):
        add_editor = FakeEditor(1)
        browser_editor = FakeEditor(2)
        self.addon._remember_editor(add_editor)
        self.addon._remember_editor(browser_editor)
        self.addon._remember_focus(add_editor, 0)

        self.assertIsNone(self.addon._active_editor())
        self.assertIs(self.addon._remembered_editor(), add_editor)

    def test_editor_with_actual_webview_focus_is_live(self):
        add_editor = FakeEditor(1)
        browser_editor = FakeEditor(2)
        self.addon._remember_editor(add_editor)
        self.addon._remember_editor(browser_editor)
        FakeApplication.focused = add_editor.web

        self.assertIs(self.addon._active_editor(), add_editor)


if __name__ == "__main__":
    unittest.main()
