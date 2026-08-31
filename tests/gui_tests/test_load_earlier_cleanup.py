# -*- coding: utf-8 -*-
"""Regression test: switching sessions must drop the stale 'Load earlier'
link from the previous session.

The 'Load earlier messages' link is a separate wx.StaticText widget bound to
a callback that captures a specific session_id. If it is not removed when the
message list is cleared, switching to a session that has no older messages
leaves the previous session's link visible — and clicking it loads the wrong
session's history (cross-session message leakage).

Skipped automatically when wx is unavailable.
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
except Exception:  # pragma: no cover
    WX_AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not WX_AVAILABLE, reason="wxPython not available"
)


@pytest.fixture(scope="module")
def wx_app():
    app = wx.App(False)
    app.SetAppName("berserker-test")
    yield app


class TestLoadEarlierCleanup(object):
    def test_clear_removes_load_earlier_link(self, wx_app):
        """MessageDisplayPanel.clear() must also remove the load-earlier link,
        not just clear the chat list."""
        from berserker.gui.main import MessageDisplayPanel

        frame = wx.Frame(None, title="load-earlier")
        panel = MessageDisplayPanel(frame)
        try:
            # Build a message list that would show the link (stub the
            # add_load_earlier_button path by calling the real one).
            panel.add_load_earlier_button(5, callback=lambda: None)
            assert panel._load_earlier_link is not None, \
                "link should be present after add_load_earlier_button"

            panel.clear()

            assert panel._load_earlier_link is None, \
                "clear() must remove the load-earlier link"
            assert panel.chat_list.get_message_count() == 0
        finally:
            frame.Destroy()

    def test_set_messages_after_clear_has_no_stale_link(self, wx_app):
        """After a session switch (clear + set_messages for a session with no
        older messages), no stale load-earlier link may remain."""
        from berserker.gui.main import MessageDisplayPanel

        frame = wx.Frame(None, title="load-earlier2")
        panel = MessageDisplayPanel(frame)
        try:
            # Session A loaded with older messages -> link created
            panel.add_load_earlier_button(3, callback=lambda: None)
            assert panel._load_earlier_link is not None

            # Switch to session B: controller calls _clear_messages() then
            # _load_session_messages(B). Session B has NO older messages, so
            # add_load_earlier_button is NOT called again.
            panel.clear()
            panel.set_messages([], scroll_to_bottom=False)

            assert panel._load_earlier_link is None, \
                "stale link from session A must NOT persist into session B"
        finally:
            frame.Destroy()
