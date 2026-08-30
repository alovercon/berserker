"""
berserker.gui.sidebar — Left sidebar panel for berserker.

Provides a sidebar showing workspace label and session list.

Python 3.8.10 compatible: uses type comments, no | union syntax.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import wx

from berserker.gui.theme import THEME, theme_hex


# Sidebar-specific color tokens (derived from the shared THEME palette)
# Values are kept in sync with THEME; use THEME directly for new code.
COLOR_SIDEBAR_BG = theme_hex("bg_sidebar")
COLOR_SIDEBAR_HEADER_BG = theme_hex("bg_primary")
COLOR_SIDEBAR_TEXT_PRIMARY = theme_hex("text_primary")  # Primary text color
COLOR_SIDEBAR_TEXT_SECONDARY = theme_hex("text_secondary")  # Secondary/label text color
COLOR_SIDEBAR_BUTTON_PRIMARY = theme_hex("accent")  # Primary button color
COLOR_SIDEBAR_BUTTON_HOVER = theme_hex("accent_hover")  # Slightly darker hover state
COLOR_SIDEBAR_SEPARATOR = theme_hex("separator")  # Subtle separator
COLOR_SIDEBAR_SECTION_LABEL = theme_hex("text_muted")  # Muted section label text


class SidebarPanel(wx.Panel):
    """Left sidebar showing workspace label and session list."""

    def __init__(self, parent, session_manager, workspace_manager):
        # type: (wx.Window, Any, Any) -> None
        super(SidebarPanel, self).__init__(parent)
        self.SetBackgroundColour(wx.Colour(COLOR_SIDEBAR_BG))
        self.SetMinSize((250, -1))

        self._session_manager = session_manager
        self._workspace_manager = workspace_manager
        self._current_workspace_id = None  # type: Optional[str]
        self._session_ids = []  # type: List[str]
        self._session_selected_callback = None  # type: Optional[Callable]
        self._new_session_callback = None  # type: Optional[Callable]
        self._delete_session_callback = None  # type: Optional[Callable]
        self._context_menu_session_idx = -1  # type: int

        # Main vertical sizer with no outer padding for clean edge-to-edge look
        sizer = wx.BoxSizer(wx.VERTICAL)

        # ------------------------------------------------------------------
        # Header area: workspace info
        # ------------------------------------------------------------------
        header_panel = wx.Panel(self)
        header_panel.SetBackgroundColour(wx.Colour(COLOR_SIDEBAR_HEADER_BG))
        header_sizer = wx.BoxSizer(wx.VERTICAL)

        # Workspace label
        self._workspace_label = wx.StaticText(header_panel, label="No Workspace")
        self._workspace_label.SetFont(
            wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD, False,
                    faceName="Segoe UI")
        )
        self._workspace_label.SetForegroundColour(wx.Colour(COLOR_SIDEBAR_TEXT_PRIMARY))
        header_sizer.Add(self._workspace_label, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 14)

        header_panel.SetSizer(header_sizer)
        sizer.Add(header_panel, 0, wx.EXPAND)

        # ------------------------------------------------------------------
        # Section label: "SESSIONS" + new session button
        section_header = wx.BoxSizer(wx.HORIZONTAL)
        section_label = wx.StaticText(self, label="SESSIONS")
        section_label.SetFont(
            wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD, False,
                    faceName="Segoe UI")
        )
        section_label.SetForegroundColour(wx.Colour(COLOR_SIDEBAR_SECTION_LABEL))
        section_header.Add(section_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.TOP | wx.BOTTOM, 2)

        section_header.AddStretchSpacer()

        # New session button with twemoji icon
        plus_bmp = self._load_twemoji("2795")  # ➕
        if plus_bmp:
            self._new_session_btn = wx.BitmapButton(
                self, bitmap=plus_bmp, size=(18, 18), style=wx.BORDER_NONE,
            )
            self._new_session_btn.SetBackgroundColour(wx.Colour(0xE8, 0xE8, 0xED))
        else:
            self._new_session_btn = wx.Button(self, label="+", size=(18, 18), style=wx.BORDER_NONE)
            self._new_session_btn.SetBackgroundColour(wx.Colour(0xE8, 0xE8, 0xED))
            self._new_session_btn.SetForegroundColour(wx.Colour(COLOR_SIDEBAR_BUTTON_PRIMARY))
            self._new_session_btn.SetFont(
                wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD, False)
            )
        self._new_session_btn.SetToolTip("New Session")
        self._new_session_btn.Bind(wx.EVT_BUTTON, self._on_new_session_click)
        section_header.Add(self._new_session_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)

        sizer.Add(section_header, 0, wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, 14)

        # ------------------------------------------------------------------
        # Session list (expands to fill available space)
        # ------------------------------------------------------------------
        self._session_list = wx.ListBox(
            self,
            style=wx.LB_SINGLE | wx.LB_HSCROLL,
        )
        self._session_list.SetFont(
            wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL, False,
                    faceName="Segoe UI")
        )
        self._session_list.SetBackgroundColour(wx.Colour(COLOR_SIDEBAR_BG))
        self._session_list.Bind(wx.EVT_LISTBOX, self._on_list_select)
        self._session_list.Bind(wx.EVT_CONTEXT_MENU, self._on_list_context_menu)
        sizer.Add(self._session_list, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

        self.SetSizer(sizer)

    @staticmethod
    def _load_twemoji(code):
        # type: (str) -> Optional[wx.Bitmap]
        """Load a twemoji PNG and scale to 16x16 for button icon."""
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "assets", "twemoji", "72x72", "{}.png".format(code))
        if os.path.exists(path):
            try:
                img = wx.Image(path, wx.BITMAP_TYPE_PNG)
                if img.IsOk():
                    img = img.Scale(16, 16, wx.IMAGE_QUALITY_HIGH)
                    return wx.Bitmap(img)
            except Exception:
                pass
        return None

    def refresh_sessions(self):
        # type: () -> None
        """Refresh session list from session manager."""
        self._session_list.Clear()
        self._session_ids = []

        if self._current_workspace_id is None:
            return

        sessions = self._session_manager.list_sessions_by_workspace(self._current_workspace_id)
        for session in sessions:
            title = session.get("title") or ""
            session_id = session.get("id", "")
            short_id = session_id[:8] if session_id else ""

            if title:
                display = "{} ({})".format(title, short_id)
            else:
                display = "(untitled) ({})".format(short_id)

            self._session_list.Append(display)
            self._session_ids.append(session_id)

    def set_workspace(self, workspace_id):
        # type: (str) -> None
        """Update workspace label and refresh sessions."""
        self._current_workspace_id = workspace_id

        # Look up workspace by ID (not by cwd, which doesn't change)
        workspace = self._workspace_manager.get_by_id(workspace_id)
        if workspace is not None:
            name = workspace.get("name", "")
            directory = workspace.get("directory", "")
            if name:
                label = name
            elif directory:
                label = directory.split("/")[-1].split("\\")[-1]
            else:
                label = workspace_id
        else:
            label = workspace_id

        self._workspace_label.SetLabel(label)
        self.refresh_sessions()

    def on_session_selected(self, callback):
        # type: (Callable) -> None
        """Register callback for session selection.

        Callback signature: callback(session_id)
        """
        self._session_selected_callback = callback

    def on_new_session(self, callback):
        # type: (Callable) -> None
        """Register callback for new session button.

        Callback signature: callback()
        """
        self._new_session_callback = callback

    def on_delete_session(self, callback):
        # type: (Callable) -> None
        """Register callback for session deletion.

        Callback signature: callback(session_id)
        """
        self._delete_session_callback = callback

    def select_session(self, session_id):
        # type: (str) -> None
        """Select a session in the list by ID.

        Finds the session in the current list and selects it visually.
        Does NOT trigger the selection callback (session is already active).
        """
        if session_id in self._session_ids:
            idx = self._session_ids.index(session_id)
            # Temporarily disconnect event to avoid triggering reload callback
            self._session_list.Unbind(wx.EVT_LISTBOX)
            self._session_list.SetSelection(idx)
            # Re-bind event handler
            self._session_list.Bind(wx.EVT_LISTBOX, self._on_list_select)

    def enable_session_list(self):
        # type: () -> None
        """Enable the session list box."""
        self._session_list.Enable()

    def disable_session_list(self):
        # type: () -> None
        """Disable the session list box."""
        self._session_list.Disable()

    def enable_new_session(self):
        # type: () -> None
        """Enable the new session button."""
        self._new_session_btn.Enable()

    def disable_new_session(self):
        # type: () -> None
        """Disable the new session button."""
        self._new_session_btn.Disable()

    def _on_list_select(self, event):
        # type: (wx.CommandEvent) -> None
        """Internal handler for listbox selection."""
        if self._session_selected_callback is None:
            return

        selection = self._session_list.GetSelection()
        if selection == wx.NOT_FOUND:
            return

        if 0 <= selection < len(self._session_ids):
            session_id = self._session_ids[selection]
            self._session_selected_callback(session_id)

    def _on_new_session_click(self, event):
        # type: (wx.CommandEvent) -> None
        """Internal handler for new session button."""
        if self._new_session_callback is not None:
            self._new_session_callback()

    def _on_list_context_menu(self, event):
        # type: (wx.ContextMenuEvent) -> None
        """Show context menu on right-click of session list."""
        # Determine which item was right-clicked
        pos = self._session_list.ScreenToClient(event.GetPosition())
        idx = self._session_list.HitTest(pos)

        if idx == wx.NOT_FOUND:
            return

        # Store the clicked index for later use
        self._context_menu_session_idx = idx

        # Build context menu
        menu = wx.Menu()
        delete_item = menu.Append(wx.ID_DELETE, "Delete Session")
        self._session_list.Bind(wx.EVT_MENU, self._on_delete_session_click, delete_item)

        self._session_list.PopupMenu(menu)
        menu.Destroy()

    def _on_delete_session_click(self, event):
        # type: (wx.CommandEvent) -> None
        """Handle delete session from context menu."""
        if self._context_menu_session_idx < 0:
            return

        if 0 <= self._context_menu_session_idx < len(self._session_ids):
            session_id = self._session_ids[self._context_menu_session_idx]
            if self._delete_session_callback is not None:
                self._delete_session_callback(session_id)
