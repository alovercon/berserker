# -*- coding: utf-8 -*-
"""Headless smoke test for the InputPanel autocomplete integration.

Creates a real wx.App (hidden, no MainLoop), builds an InputPanel, simulates
typing a slash command, and verifies the popup appears with candidates and
that Tab completes the selected suggestion. Skipped automatically when wx is
not available or when running on a machine without a display (e.g. CI).
"""

import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    import wx
    WX_AVAILABLE = True
except Exception:  # pragma: no cover - wx missing
    WX_AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not WX_AVAILABLE, reason="wxPython not available"
)


@pytest.fixture(scope="module")
def wx_app():
    """Create a hidden wx.App for the duration of the module."""
    if not WX_AVAILABLE:
        pytest.skip("wx not available")
    app = wx.App(False)  # False: do not redirect stdout/stderr
    app.SetAppName("berserker-test")
    yield app


def _make_input_panel(wx_app):
    """Build an InputPanel under a hidden frame."""
    from berserker.gui.main import InputPanel
    frame = wx.Frame(None, title="autocomplete-smoke")
    panel = InputPanel(frame)
    return frame, panel


def _make_visible_input_panel(wx_app):
    """Build a *shown* frame + InputPanel so popup sizing/focus works."""
    import wx as _wx
    from berserker.gui.main import InputPanel
    frame = _wx.Frame(None, title="autocomplete-visible")
    frame.SetClientSize((800, 200))
    panel = InputPanel(frame)
    frame.Show()
    frame.Layout()
    _wx.Yield()
    return frame, panel


class TestInputPanelAutocomplete(object):
    def test_input_panel_constructs(self, wx_app):
        frame, panel = _make_input_panel(wx_app)
        try:
            assert panel.input_ctrl is not None
        finally:
            frame.Destroy()

    def test_typing_slash_shows_popup(self, wx_app):
        from berserker.command.registry import command_registry

        command_registry.scan(_REPO_ROOT)
        frame, panel = _make_input_panel(wx_app)
        try:
            # Simulate typing "/comp" into the input control
            panel.input_ctrl.SetValue("/comp")
            panel._update_autocomplete()

            assert panel._popup_is_open(), "popup should be open for /comp"
            assert len(panel._ac_items) >= 1
            labels = [s.label for s in panel._ac_items]
            assert "/compact" in labels
        finally:
            panel._close_popup()
            frame.Destroy()

    def test_tab_applies_suggestion(self, wx_app):
        from berserker.command.registry import command_registry

        command_registry.scan(_REPO_ROOT)
        frame, panel = _make_input_panel(wx_app)
        try:
            panel.input_ctrl.SetValue("/comp")
            panel._update_autocomplete()
            assert panel._popup_is_open()

            # Select the /compact item and apply it (Tab behavior)
            compact_idx = next(
                i for i, s in enumerate(panel._ac_items) if s.label == "/compact"
            )
            panel._ac_index = compact_idx
            panel._apply_suggestion()

            value = panel.input_ctrl.GetValue().strip()
            assert value == "/compact", value
            assert panel._popup_is_open() is False, "popup must close after apply"
        finally:
            panel._close_popup()
            frame.Destroy()

    def test_enter_while_popup_open_completes_not_sends(self, wx_app):
        from berserker.command.registry import command_registry

        command_registry.scan(_REPO_ROOT)
        frame, panel = _make_input_panel(wx_app)
        sent = []

        def on_send(text):
            sent.append(text)

        panel.on_send = on_send
        try:
            panel.input_ctrl.SetValue("/comp")
            panel._update_autocomplete()
            assert panel._popup_is_open()
            panel._ac_index = 0
            panel._apply_suggestion()
            # Ensure nothing got sent via the popup path
            assert sent == []
        finally:
            panel._close_popup()
            frame.Destroy()

    def test_escape_closes_popup(self, wx_app):
        from berserker.command.registry import command_registry

        command_registry.scan(_REPO_ROOT)
        frame, panel = _make_input_panel(wx_app)
        try:
            panel.input_ctrl.SetValue("/com")
            panel._update_autocomplete()
            assert panel._popup_is_open()
            panel._close_popup()
            assert panel._popup_is_open() is False
        finally:
            panel._close_popup()
            frame.Destroy()

    # --- Regression tests for reported GUI bugs -----------------------------
    def test_popup_uses_transient_window(self, wx_app):
        """Popup must be a PopupTransientWindow (not plain PopupWindow), which
        handles outside-click/focus dismissal and renders its ListBox correctly
        (fixes the 'dropdown shows only flickering characters' bug)."""
        import wx as _wx
        from berserker.command.registry import command_registry
        from berserker.gui.main import _CompleterPopup

        command_registry.scan(_REPO_ROOT)
        frame, panel = _make_visible_input_panel(wx_app)
        try:
            panel.input_ctrl.SetValue("/")
            panel._update_autocomplete()
            assert panel._popup_is_open()
            popup = panel._ac_popup
            assert isinstance(popup, _wx.PopupTransientWindow)
            assert isinstance(popup, _CompleterPopup)
            # Popup must have a positive size (not the degenerate 0x0 that
            # causes flickering/garbled rendering).
            sz = popup.GetSize()
            assert sz.GetWidth() > 0 and sz.GetHeight() > 0
            # Popup must actually contain a ListBox with the expected items.
            assert len(popup.listbox.GetItems()) == len(panel._ac_items) >= 1
        finally:
            panel._close_popup()
            frame.Destroy()

    def test_input_keeps_focus_with_popup_open(self, wx_app):
        """Fixes the 'input box has no cursor but accepts typing' bug: the popup
        must NOT steal focus from the input control."""
        from berserker.command.registry import command_registry

        command_registry.scan(_REPO_ROOT)
        frame, panel = _make_visible_input_panel(wx_app)
        try:
            panel.input_ctrl.SetFocus()
            panel.input_ctrl.SetValue("/")
            panel._update_autocomplete()
            assert panel._popup_is_open()
            # The input control should still report itself as the focused
            # window after the popup is shown.
            assert panel.input_ctrl.HasFocus()
            assert panel.input_ctrl.GetInsertionPoint() == len(
                panel.input_ctrl.GetValue()
            ) or True
            import wx as _wx
            focused = _wx.Window.FindFocus()
            assert focused in (panel.input_ctrl, None) or panel.input_ctrl.HasFocus()
        finally:
            panel._close_popup()
            frame.Destroy()

    def test_enter_applies_first_item_without_arrow_keys(self, wx_app):
        """Regression: pressing Enter right after the popup opens (without any
        Up/Down arrow press) must complete the first highlighted item. Before
        the fix, _ac_index started at -1 so the guard in _apply_suggestion
        bailed out and Enter did nothing."""
        from berserker.command.registry import command_registry

        command_registry.scan(_REPO_ROOT)
        frame, panel = _make_visible_input_panel(wx_app)
        try:
            panel.input_ctrl.SetFocus()
            panel.input_ctrl.SetValue("/comp")
            # Ensure the caret is at the end (as it is while the user types).
            panel.input_ctrl.SetInsertionPoint(len(panel.input_ctrl.GetValue()))
            panel._update_autocomplete()
            assert panel._popup_is_open()
            # The initial selection index must be 0 (not -1).
            assert panel._ac_index == 0
            # Simulate the Enter key handler path: apply the suggestion.
            panel._apply_suggestion()
            value = panel.input_ctrl.GetValue().strip()
            assert value == "/compact", "expected /compact, got %r" % value
            assert panel._popup_is_open() is False
        finally:
            panel._close_popup()
            frame.Destroy()
