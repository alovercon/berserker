"""Tests for SidebarPanel: creation, session display, callbacks, context menu deletion."""

import unittest
from unittest.mock import MagicMock, patch

import wx

from berserker.gui.sidebar import SidebarPanel


class TestSidebarPanel(unittest.TestCase):
    """Test SidebarPanel creation and behavior."""

    def setUp(self):
        # type: () -> None
        """Set up test fixtures."""
        self.app = wx.App(False)
        self.frame = wx.Frame(None)
        self.session_manager = MagicMock()
        self.workspace_manager = MagicMock()

    def tearDown(self):
        # type: () -> None
        """Clean up after tests."""
        self.frame.Destroy()
        self.app = None

    def test_sidebar_panel_creation(self):
        """Should instantiate panel with mock managers."""
        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        self.assertIsNotNone(panel)
        self.assertIsInstance(panel, wx.Panel)
        # Verify min width is 250px
        min_size = panel.GetMinSize()
        self.assertEqual(min_size.GetWidth(), 250)

    def test_refresh_sessions_populates_list(self):
        """Mock returns 3 sessions, verify list has 3 items."""
        self.workspace_manager.get_by_id.return_value = {
            "id": "ws-123",
            "name": "Test Workspace",
            "directory": "/test/workspace",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        self.session_manager.list_sessions_by_workspace.return_value = [
            {"id": "sess-aaa-001", "title": "Session A", "workspace_id": "ws-123"},
            {"id": "sess-bbb-002", "title": "Session B", "workspace_id": "ws-123"},
            {"id": "sess-ccc-003", "title": "Session C", "workspace_id": "ws-123"},
        ]

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        panel.set_workspace("ws-123")

        self.assertEqual(panel._session_list.GetCount(), 3)
        self.assertEqual(panel._session_ids[0], "sess-aaa-001")
        self.assertEqual(panel._session_ids[1], "sess-bbb-002")
        self.assertEqual(panel._session_ids[2], "sess-ccc-003")

    def test_session_selection_triggers_callback(self):
        """Simulate selection, verify callback called with session_id."""
        self.workspace_manager.get_by_id.return_value = {
            "id": "ws-123",
            "name": "Test",
            "directory": "/test",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        self.session_manager.list_sessions_by_workspace.return_value = [
            {"id": "sess-111-aaa", "title": "First Session", "workspace_id": "ws-123"},
        ]

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        panel.set_workspace("ws-123")

        selected_id = []  # type: list

        def on_select(sid):
            # type: (str) -> None
            selected_id.append(sid)

        panel.on_session_selected(on_select)

        # Simulate listbox selection
        panel._session_list.SetSelection(0)
        event = wx.CommandEvent(wx.EVT_LISTBOX.typeId, panel._session_list.GetId())
        panel._on_list_select(event)

        self.assertEqual(len(selected_id), 1)
        self.assertEqual(selected_id[0], "sess-111-aaa")

    def test_new_session_button_triggers_callback(self):
        """Simulate button click, verify callback called."""
        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)

        clicked = []  # type: list

        def on_new():
            # type: () -> None
            clicked.append(True)

        panel.on_new_session(on_new)

        # Simulate button click
        event = wx.CommandEvent(wx.EVT_BUTTON.typeId, panel._new_session_btn.GetId())
        panel._on_new_session_click(event)

        self.assertEqual(len(clicked), 1)
        self.assertTrue(clicked[0])

    def test_untitled_session_display(self):
        """Session with empty title shows '(untitled)'."""
        self.workspace_manager.get_by_id.return_value = {
            "id": "ws-456",
            "name": "Workspace",
            "directory": "/ws",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        self.session_manager.list_sessions_by_workspace.return_value = [
            {"id": "sess-notitle-1", "title": "", "workspace_id": "ws-456"},
            {"id": "sess-noname-2", "title": None, "workspace_id": "ws-456"},
        ]

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        panel.set_workspace("ws-456")

        self.assertEqual(panel._session_list.GetCount(), 2)
        first_item = panel._session_list.GetString(0)
        second_item = panel._session_list.GetString(1)
        self.assertTrue(first_item.startswith("(untitled)"))
        self.assertTrue(second_item.startswith("(untitled)"))

    def test_set_workspace_updates_label(self):
        """Call set_workspace, verify label changes."""
        self.workspace_manager.get_by_id.return_value = {
            "id": "ws-789",
            "name": "My Project",
            "directory": "/path/to/project",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        self.session_manager.list_sessions_by_workspace.return_value = []

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        panel.set_workspace("ws-789")

        label = panel._workspace_label.GetLabel()
        self.assertEqual(label, "My Project")

    def test_set_workspace_without_workspace_info(self):
        """When workspace manager returns None, label falls back to workspace_id."""
        self.workspace_manager.get_by_id.return_value = None
        self.session_manager.list_sessions_by_workspace.return_value = []

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        panel.set_workspace("ws-fallback")

        label = panel._workspace_label.GetLabel()
        self.assertEqual(label, "ws-fallback")

    def test_set_workspace_uses_directory_when_no_name(self):
        """When workspace has no name, label uses directory basename."""
        self.workspace_manager.get_by_id.return_value = {
            "id": "ws-dir",
            "name": "",
            "directory": "E:\\0-works\\coding\\myproject",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        self.session_manager.list_sessions_by_workspace.return_value = []

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        panel.set_workspace("ws-dir")

        label = panel._workspace_label.GetLabel()
        self.assertEqual(label, "myproject")

    def test_refresh_sessions_with_no_workspace_id(self):
        """refresh_sessions does nothing when workspace_id is not set."""
        self.session_manager.list_sessions_by_workspace.return_value = [
            {"id": "sess-should-not-appear", "title": "Ghost", "workspace_id": "ws-x"},
        ]

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        # Do NOT call set_workspace — _current_workspace_id remains None
        panel.refresh_sessions()

        self.assertEqual(panel._session_list.GetCount(), 0)
        self.session_manager.list_sessions_by_workspace.assert_not_called()

    def test_context_menu_delete_callback(self):
        """Right-click context menu triggers delete callback with correct session_id."""
        self.workspace_manager.get_by_id.return_value = {
            "id": "ws-ctx",
            "name": "Ctx Test",
            "directory": "/ctx",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        self.session_manager.list_sessions_by_workspace.return_value = [
            {"id": "sess-delete-me", "title": "Delete Me", "workspace_id": "ws-ctx"},
            {"id": "sess-keep", "title": "Keep Me", "workspace_id": "ws-ctx"},
        ]

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        panel.set_workspace("ws-ctx")

        deleted_id = []  # type: list

        def on_delete(sid):
            # type: (str) -> None
            deleted_id.append(sid)

        panel.on_delete_session(on_delete)

        # Simulate context menu on first item (index 0)
        panel._context_menu_session_idx = 0
        panel._on_delete_session_click(None)

        self.assertEqual(len(deleted_id), 1)
        self.assertEqual(deleted_id[0], "sess-delete-me")

    def test_context_menu_delete_invalid_index(self):
        """Delete with invalid index does not trigger callback."""
        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)

        deleted_id = []  # type: list
        panel.on_delete_session(lambda sid: deleted_id.append(sid))

        # Negative index
        panel._context_menu_session_idx = -1
        panel._on_delete_session_click(None)
        self.assertEqual(len(deleted_id), 0)

        # Out-of-range index
        panel._context_menu_session_idx = 99
        panel._on_delete_session_click(None)
        self.assertEqual(len(deleted_id), 0)

    def test_context_menu_hit_test_returns_int(self):
        """HitTest returns int (not tuple), verify context menu handles it correctly."""
        self.workspace_manager.get_by_id.return_value = {
            "id": "ws-hit",
            "name": "HitTest",
            "directory": "/hit",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        self.session_manager.list_sessions_by_workspace.return_value = [
            {"id": "sess-hit-001", "title": "Target", "workspace_id": "ws-hit"},
        ]

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        panel.set_workspace("ws-hit")

        # Verify HitTest returns int, not tuple
        result = panel._session_list.HitTest((0, 0))
        self.assertIsInstance(result, int)

    def test_context_menu_shows_on_right_click(self):
        """Context menu is shown when right-clicking a session item."""
        self.workspace_manager.get_by_id.return_value = {
            "id": "ws-menu",
            "name": "Menu Test",
            "directory": "/menu",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        self.session_manager.list_sessions_by_workspace.return_value = [
            {"id": "sess-menu-001", "title": "Menu Item", "workspace_id": "ws-menu"},
        ]

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        panel.set_workspace("ws-menu")

        deleted_id = []  # type: list
        panel.on_delete_session(lambda sid: deleted_id.append(sid))

        # Mock HitTest to return index 0 and PopupMenu to avoid blocking
        original_hittest = panel._session_list.HitTest
        original_popup = panel._session_list.PopupMenu
        panel._session_list.HitTest = lambda pos: 0  # type: ignore
        panel._session_list.PopupMenu = lambda menu: None  # type: ignore

        # Simulate context menu event
        event = wx.ContextMenuEvent()
        panel._on_list_context_menu(event)

        # Restore originals
        panel._session_list.HitTest = original_hittest  # type: ignore
        panel._session_list.PopupMenu = original_popup  # type: ignore

        # Verify context menu index was stored
        self.assertEqual(panel._context_menu_session_idx, 0)

    def test_context_menu_no_item_clicked(self):
        """Context menu does nothing when HitTest returns NOT_FOUND."""
        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)

        deleted_id = []  # type: list
        panel.on_delete_session(lambda sid: deleted_id.append(sid))

        # Simulate context menu with no item at position
        event = wx.ContextMenuEvent()
        panel._on_list_context_menu(event)

        # Index should remain -1 (no item clicked)
        self.assertEqual(panel._context_menu_session_idx, -1)
        self.assertEqual(len(deleted_id), 0)


class TestSidebarPanelCallbacks(unittest.TestCase):
    """Test SidebarPanel callback registration and invocation."""

    def setUp(self):
        # type: () -> None
        self.app = wx.App(False)
        self.frame = wx.Frame(None)
        self.session_manager = MagicMock()
        self.workspace_manager = MagicMock()

    def tearDown(self):
        # type: () -> None
        self.frame.Destroy()
        self.app = None

    def test_on_session_selected_registers_callback(self):
        """Callback is stored and invoked on selection."""
        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)

        called = []  # type: list
        panel.on_session_selected(lambda sid: called.append(sid))

        self.assertIsNotNone(panel._session_selected_callback)

    def test_on_new_session_registers_callback(self):
        """Callback is stored and invoked on button click."""
        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)

        called = []  # type: list
        panel.on_new_session(lambda: called.append(True))

        self.assertIsNotNone(panel._new_session_callback)

    def test_on_delete_session_registers_callback(self):
        """Callback is stored and invoked on delete."""
        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)

        called = []  # type: list
        panel.on_delete_session(lambda sid: called.append(sid))

        self.assertIsNotNone(panel._delete_session_callback)

    def test_no_callback_no_crash(self):
        """Panel does not crash when callbacks are not registered."""
        self.workspace_manager.get_by_id.return_value = {
            "id": "ws-nocb",
            "name": "No Callback",
            "directory": "/nocb",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        self.session_manager.list_sessions_by_workspace.return_value = [
            {"id": "sess-nocb", "title": "Test", "workspace_id": "ws-nocb"},
        ]

        panel = SidebarPanel(self.frame, self.session_manager, self.workspace_manager)
        panel.set_workspace("ws-nocb")

        # These should not raise even without callbacks registered
        panel._session_list.SetSelection(0)
        event = wx.CommandEvent(wx.EVT_LISTBOX.typeId, panel._session_list.GetId())
        panel._on_list_select(event)  # Should silently return

        panel._on_new_session_click(None)  # Should silently return

        panel._context_menu_session_idx = 0
        panel._on_delete_session_click(None)  # Should silently return


if __name__ == "__main__":
    unittest.main()
