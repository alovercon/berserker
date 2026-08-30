"""

berserker.gui.main — wxPython GUI main frame for berserker.



Provides a modern, clean chat interface with:

- Message display area (top)

- Dynamic widget panel for selections (middle, hidden when empty)

- Input area with Enter=send, Shift+Enter=newline (bottom)



Python 3.8.10 compatible: uses type comments, no | union syntax.

"""



from __future__ import annotations



import logging

import os

import sys

import threading

from datetime import datetime

from typing import Any, Callable, Dict, List, Optional



import wx



logger = logging.getLogger(__name__)

import wx.adv

import wx.html

import wx.lib.scrolledpanel



from berserker.gui.chat_listbox import ChatListBox, ChatMessageData

from berserker.gui.agent_status_panel import AgentStatusPanel

from berserker.gui.autocomplete import Suggestion, get_candidates, get_query_from_text



# ---------------------------------------------------------------------------

# Constants

# ---------------------------------------------------------------------------



# Proportional layout ratios for the 3-panel splitter layout.

# SIDEBAR_RATIO: fraction of total window width allocated to the left sidebar.

# AGENT_STATUS_RATIO: fraction of total window width for the right agent status panel.

# The main content panel takes the remaining width.

SIDEBAR_RATIO = 0.18        # 18% — left sidebar

AGENT_STATUS_RATIO = 0.30   # 30% — right agent status panel

# Main content: 1 - 0.18 - 0.30 = 52%

COLOR_BG = "#F0F0F0"  # Light gray background

FONT_FAMILY = (

    sys.platform == "win32" and "Segoe UI" or (sys.platform == "darwin" and "SF Pro Text" or "Sans")

)



# Theme: modern blue accent palette

_THEME = {

    "accent": wx.Colour(0, 122, 255),       # #007AFF — iOS blue

    "accent_hover": wx.Colour(0, 102, 230), # Darker blue for hover

    "danger": wx.Colour(255, 59, 48),       # #FF3B30 — iOS red

    "disabled": wx.Colour(199, 199, 204),   # #C7C7CC — gray

    "bg_primary": wx.Colour(240, 240, 240), # #F0F0F0 — main bg

    "bg_sidebar": wx.Colour(235, 235, 240), # #EBEBF0 — sidebar bg

    "bg_card": wx.Colour(255, 255, 255),    # #FFFFFF — card/panel bg

    "text_primary": wx.Colour(29, 29, 31),  # #1D1D1F

    "text_secondary": wx.Colour(110, 110, 115),  # #6E6E73

    "separator": wx.Colour(209, 209, 214),  # #D1D1D6

}

PADDING = 10


# ---------------------------------------------------------------------------

# Search Bar Panel# ---------------------------------------------------------------------------



class SearchBarPanel(wx.Panel):

    """Search bar with SearchCtrl for finding messages."""



    def __init__(self, parent, on_search=None, on_close=None):

        # type: (wx.Window, Optional[Callable], Optional[Callable]) -> None

        super(SearchBarPanel, self).__init__(parent, style=wx.BORDER_SIMPLE)

        self.SetBackgroundColour(wx.Colour("#FFFFFF"))

        self.on_search = on_search

        self.on_close = on_close



        sizer = wx.BoxSizer(wx.HORIZONTAL)



        # SearchCtrl with built-in search icon and cancel button

        self.search_ctrl = wx.SearchCtrl(self)

        self.search_ctrl.SetDescriptiveText("Search messages... (Esc to close)")

        self.search_ctrl.Bind(wx.EVT_TEXT, self._on_search_text)

        self.search_ctrl.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self._on_search)

        self.search_ctrl.Bind(wx.EVT_CHAR, self._on_char)



        # Close button

        close_btn = wx.Button(self, label="×", style=wx.NO_BORDER)

        close_btn.SetMinSize((30, -1))

        close_btn.Bind(wx.EVT_BUTTON, self._on_close)



        sizer.Add(self.search_ctrl, 1, wx.EXPAND | wx.ALL, 4)

        sizer.Add(close_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)



        self.SetSizer(sizer)

        self.search_ctrl.SetFocus()



    def _on_search_text(self, event):

        # type: (wx.CommandEvent) -> None

        """Handle search text change."""

        if self.on_search:

            self.on_search(self.search_ctrl.GetValue())



    def _on_search(self, event):

        # type: (wx.CommandEvent) -> None

        """Handle search button click."""

        if self.on_search:

            self.on_search(self.search_ctrl.GetValue())



    def _on_char(self, event):

        # type: (wx.KeyEvent) -> None

        """Handle key events: Esc to close."""

        if event.GetKeyCode() == wx.WXK_ESCAPE:

            self._on_close(None)

        else:

            event.Skip()



    def _on_close(self, event):

        # type: (Optional[wx.CommandEvent]) -> None

        """Close the search bar."""

        if self.on_close:

            self.on_close()



    def get_value(self):

        # type: () -> str

        """Get current search text."""

        return self.search_ctrl.GetValue()



    def clear(self):

        # type: () -> None

        """Clear search text."""

        self.search_ctrl.Clear()



# ---------------------------------------------------------------------------

# Message Display Panel (VListBox-based)

# ---------------------------------------------------------------------------



class MessageDisplayPanel(wx.Panel):

    """Scrollable chat message display using ChatListBox (wx.VListBox)."""



    def __init__(self, parent):

        # type: (wx.Window) -> None

        super(MessageDisplayPanel, self).__init__(parent)

        self.SetBackgroundColour(wx.Colour(0xF0, 0xF0, 0xF0))

        sizer = wx.BoxSizer(wx.VERTICAL)

        self.chat_list = ChatListBox(self)

        sizer.Add(self.chat_list, 1, wx.EXPAND)

        self.SetSizer(sizer)

        self._load_earlier_link = None  # type: Optional[wx.Window]

    def add_user_message(self, text, defer_layout=False, timestamp=None):
        # type: (str, bool, Optional[str]) -> None
        self.chat_list.add_message("user", text, timestamp=timestamp)

    def add_assistant_message(self, text, defer_layout=False, timestamp=None):
        # type: (str, bool, Optional[str]) -> None
        self.chat_list.add_message("assistant", text, timestamp=timestamp)

    def add_tool_call(self, tool_name, result, defer_layout=False, timestamp=None):
        # type: (str, dict, bool, Optional[str]) -> None
        output = (result.get("output", "") or "")
        error = (result.get("error", "") or "")
        title = result.get("title", "")
        if title:
            # Use explicit title from tool (e.g., task tool start notification)
            header = title
        elif error:
            header = "[{}] Error: {}".format(tool_name, error)
        else:
            header = "[{}]".format(tool_name)

        display_text = header
        if output:
            display_text = "{}\n{}".format(header, output)
        self.chat_list.add_message("tool", display_text, timestamp=timestamp, tool_name=tool_name)


    def search_messages(self, query):

        # type: (str) -> int

        return self.chat_list.search(query)



    def _clear_search_highlights(self):

        # type: () -> None

        self.chat_list.clear_search()



    def set_messages(self, items, scroll_to_bottom=False):
        # type: (list, bool) -> None
        """Replace the entire message list in one batch (main thread only)."""
        self.chat_list.set_messages(items)
        if scroll_to_bottom:
            self.chat_list.scroll_to_bottom()

    def insert_messages_at_front(self, items):
        # type: (list) -> None
        """Insert older messages at the front, preserving the viewport (main thread only)."""
        self.chat_list.insert_front(items)

    def clear(self):
        # type: () -> None
        self.chat_list.clear()

    def add_load_earlier_button(self, remaining_count, callback=None):
        # type: (int, Optional[Callable[[], None]]) -> None

        """Add a 'Load earlier messages' link above the chat list."""

        self.remove_load_earlier_button()



        link_text = "Load earlier messages ({} remaining)".format(remaining_count)

        link = wx.StaticText(self, label=link_text)

        link.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        link.SetForegroundColour(wx.Colour(0x00, 0x7A, 0xFF))

        link.SetCursor(wx.Cursor(wx.CURSOR_HAND))

        link.Bind(wx.EVT_LEFT_DOWN, lambda e, cb=callback: cb() if cb else None)



        # Wrap in horizontal sizer for centering

        h_sizer = wx.BoxSizer(wx.HORIZONTAL)

        h_sizer.AddStretchSpacer()

        h_sizer.Add(link, 0, wx.TOP | wx.BOTTOM, 8)

        h_sizer.AddStretchSpacer()



        # Insert at top of the vertical sizer (before chat_list)

        self.GetSizer().Insert(0, h_sizer, 0, wx.EXPAND)

        self._load_earlier_link = link

        self.Layout()



    def remove_load_earlier_button(self):

        # type: () -> None

        """Remove the 'Load earlier messages' link."""

        link = self._load_earlier_link

        self._load_earlier_link = None

        if link is None:

            return

        try:

            parent_sizer = link.GetContainingSizer()

            if parent_sizer:

                # Remove the h_sizer from our sizer

                self.GetSizer().Detach(parent_sizer)

                # Destroy the link widget

                link.Destroy()

            else:

                link.Destroy()

        except Exception:

            try:

                link.Destroy()

            except Exception:

                pass

        self.Layout()

# ---------------------------------------------------------------------------
# Dynamic Widget Panel (Selections)
# ---------------------------------------------------------------------------
class DynamicWidgetPanel(wx.Panel):

    """Panel that dynamically shows selection widgets when agent requires user input."""



    def __init__(self, parent):

        # type: (wx.Window) -> None

        super(DynamicWidgetPanel, self).__init__(parent, style=wx.BORDER_SIMPLE)

        self.SetBackgroundColour(wx.Colour("#FAFAFA"))



        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.SetSizer(self.sizer)



        # Selection widgets (created lazily)

        self.selection_label = wx.StaticText(self, label="")

        self.selection_label.Hide()

        self.selection_radio = None  # type: Optional[wx.RadioBox]

        self.selection_checkboxes = []  # type: List[wx.CheckBox]

        self.select_all_checkbox = None  # type: Optional[wx.CheckBox]

        self.selection_custom = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)

        self.selection_custom.Hide()

        self._custom_hint = wx.StaticText(

            self, label="或在下方输入自定义内容：",

            style=wx.ST_NO_AUTORESIZE)

        self._custom_hint.SetForegroundColour(wx.Colour(0x8E, 0x8E, 0x93))

        self._custom_hint.SetFont(wx.Font(

            9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        self._custom_hint.Hide()

        self.selection_confirm_btn = None  # type: Optional[wx.Button]

        self._confirm_btn_panel = None  # type: Optional[wx.Panel]



        # Selection mode state

        self._multiple = False

        self._selection_options = []  # type: List[Dict[str, str]]

        self._selection_callback = None  # type: Optional[Callable]



        # Permission mode state

        self._allow_all_session = False  # type: bool

        self._permission_callback = None  # type: Optional[Callable]

        self._permission_tool_name = ""  # type: str

        self._permission_args = {}  # type: Dict[str, Any]



        # Permission widgets (created lazily)

        self.permission_label = wx.StaticText(self, label="")

        self.permission_label.Hide()

        self.permission_args_text = wx.StaticText(

            self, label="", style=wx.TE_READONLY | wx.BORDER_NONE

        )

        self.permission_args_text.SetForegroundColour(wx.Colour("#6B7280"))

        self.permission_args_text.Hide()

        self.permission_btn_allow = None  # type: Optional[wx.Button]

        self.permission_btn_deny = None  # type: Optional[wx.Button]

        self.permission_btn_allow_all = None  # type: Optional[wx.Button]

        self._permission_btn_panel = None  # type: Optional[wx.Panel]



        self.sizer.Add(self.selection_label, 0, wx.LEFT | wx.TOP, PADDING)

        # selection_radio or checkboxes added lazily in show_selection()

        self.sizer.Add(

            self._custom_hint, 0, wx.LEFT | wx.RIGHT | wx.TOP, PADDING

        )

        self.sizer.Add(

            self.selection_custom, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, PADDING

        )



        # Permission widgets (added to sizer, initially hidden)

        self.sizer.Add(self.permission_label, 0, wx.LEFT | wx.TOP, PADDING)

        self.sizer.Add(self.permission_args_text, 0, wx.LEFT | wx.RIGHT | wx.TOP, PADDING)

        # btn_panel added lazily in show_tool_permission()



        # Initially hidden (height=0)

        self.Hide()



    def show_selection(self, question, options, multiple=False, allow_custom=False, on_select=None):

        # type: (str, List[Dict[str, str]], bool, bool, Optional[Callable]) -> None

        """Show selection widget.



        Args:

            question: The question/prompt to display.

            options: List of option dicts with 'label' and optionally 'value'.

            multiple: If True, show checkboxes + Select All. If False, show RadioBox.

            allow_custom: If True, show custom text input.

            on_select: Callback called when selection changes.

        """

        # Store state for checkbox mode

        self._multiple = multiple

        self._selection_options = options

        self._selection_callback = on_select



        # Show selection label

        self.selection_label.SetLabel(question)

        self.selection_label.Show()



        # Clean up existing selection widgets

        self._clear_selection_widgets()



        choices = [opt.get("label", "") for opt in options]



        if multiple:

            self._create_checkbox_selection(options, allow_custom, on_select)

        else:

            self._create_radio_selection(choices, allow_custom, on_select)



        self.Show()

        self.GetParent().Layout()



    def _clear_selection_widgets(self):

        # type: () -> None

        """Clear existing selection widgets from the sizer."""

        # Destroy RadioBox if exists

        if self.selection_radio is not None:

            self.selection_radio.Destroy()

            self.selection_radio = None



        # Destroy checkboxes if exist

        for cb in self.selection_checkboxes:

            cb.Destroy()

        self.selection_checkboxes = []



        # Destroy Select All checkbox if exists

        if self.select_all_checkbox is not None:

            self.select_all_checkbox.Destroy()

            self.select_all_checkbox = None



        # Clear confirm button

        self._clear_confirm_button()



    def _create_checkbox_selection(self, options, allow_custom, on_select):

        # type: (List[Dict[str, str]], bool, Optional[Callable]) -> None

        """Create checkbox UI for multiple selection mode."""

        checkbox_panel = wx.Panel(self)

        checkbox_sizer = wx.BoxSizer(wx.VERTICAL)



        self.select_all_checkbox = wx.CheckBox(checkbox_panel, label="全选")

        self.select_all_checkbox.Bind(wx.EVT_CHECKBOX, self._on_select_all_toggle)

        checkbox_sizer.Add(self.select_all_checkbox, 0, wx.BOTTOM, 4)



        for i, opt in enumerate(options):

            cb = wx.CheckBox(checkbox_panel, label=opt.get("label", ""))

            # Don't trigger callback on change — wait for confirm

            checkbox_sizer.Add(cb, 0, wx.BOTTOM, 2)

            self.selection_checkboxes.append(cb)



        checkbox_panel.SetSizer(checkbox_sizer)

        self.sizer.Insert(1, checkbox_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, PADDING)



        if allow_custom:

            self.selection_custom.Show()

            self._custom_hint.Show()

        else:

            self.selection_custom.Hide()

            self._custom_hint.Hide()



        # Add confirm button

        self._add_confirm_button(on_select)



    def _create_radio_selection(self, choices, allow_custom, on_select):

        # type: (List[str], bool, Optional[Callable]) -> None

        """Create RadioBox UI for single selection mode."""

        self.selection_radio = wx.RadioBox(

            self, label="", choices=choices or ["(no options)"], style=wx.RA_SPECIFY_ROWS

        )

        # Insert before hint (index 1)

        self.sizer.Insert(1, self.selection_radio, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, PADDING)



        if allow_custom:

            self.selection_custom.Show()

            self._custom_hint.Show()

        else:

            self.selection_custom.Hide()

            self._custom_hint.Hide()



        self._add_confirm_button(on_select)



    def _on_select_all_toggle(self, event):

        # type: (wx.CommandEvent) -> None

        """Handle Select All checkbox toggle — just update checkboxes."""

        is_checked = self.select_all_checkbox.GetValue() if self.select_all_checkbox else False

        for cb in self.selection_checkboxes:

            cb.SetValue(is_checked)



    def _on_checkbox_change(self, callback, options):

        # type: (Optional[Callable], List[Dict[str, str]]) -> None

        """Handle checkbox state change."""

        if callback:

            selected_indices = [

                i for i, cb in enumerate(self.selection_checkboxes) if cb.GetValue()

            ]

            callback({"selected": selected_indices})



    def hide(self):

        # type: () -> None

        """Hide the dynamic widget panel."""

        self.Hide()

        self.selection_label.Hide()

        if self.selection_radio:

            self.selection_radio.Hide()

        for cb in self.selection_checkboxes:

            cb.Hide()

        if self.select_all_checkbox:

            self.select_all_checkbox.Hide()

        self.selection_custom.Hide()

        self._custom_hint.Hide()

        self._clear_confirm_button()

        self.permission_label.Hide()

        self.permission_args_text.Hide()

        self._clear_permission_buttons()

        self.GetParent().Layout()



    def _add_confirm_button(self, callback):

        # type: (Optional[Callable]) -> None

        """Add a Confirm button below selection widgets."""

        self._clear_confirm_button()

        btn_panel = wx.Panel(self)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        btn_sizer.AddStretchSpacer()

        confirm_btn = wx.Button(btn_panel, label="确认选择")

        confirm_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_confirm(callback))

        btn_sizer.Add(confirm_btn)

        btn_panel.SetSizer(btn_sizer)

        self.sizer.Insert(4, btn_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, PADDING)

        self.selection_confirm_btn = confirm_btn

        self._confirm_btn_panel = btn_panel



    def _clear_confirm_button(self):

        # type: () -> None

        """Remove the confirm button panel from sizer."""

        if self._confirm_btn_panel:

            try:

                self.sizer.Detach(self._confirm_btn_panel)

                self._confirm_btn_panel.Destroy()

            except Exception:

                pass

            self._confirm_btn_panel = None

            self.selection_confirm_btn = None



    def _on_confirm(self, callback):

        # type: (Optional[Callable]) -> None

        """Handle confirm button click — collect selection and call callback."""

        if not callback:

            return



        selected = []  # type: List[int]

        if self.selection_radio is not None:

            idx = self.selection_radio.GetSelection()

            if idx != wx.NOT_FOUND and idx >= 0:

                selected = [idx]

        elif self.selection_checkboxes:

            selected = [i for i, cb in enumerate(self.selection_checkboxes) if cb.GetValue()]



        custom = self.selection_custom.GetValue().strip() if self.selection_custom.IsShown() else ""



        if custom and not selected:

            callback({"selected": [], "custom": custom})

        elif selected:

            callback({"selected": selected, "custom": custom if custom else ""})

        else:

            # Nothing selected — flash hint on button

            if self.selection_confirm_btn:

                old = self.selection_confirm_btn.GetLabel()

                hint = "请选择或输入内容" if self.selection_custom.IsShown() else "请先选择一项"

                self.selection_confirm_btn.SetLabel(hint)

                wx.CallLater(2000, lambda b=self.selection_confirm_btn, l=old: b.SetLabel(l) if b else None)



    # -----------------------------------------------------------------------

    # Tool Permission Management

    # -----------------------------------------------------------------------



    def show_tool_permission(self, tool_name, args, on_respond=None):

        # type: (str, Dict[str, Any], Optional[Callable]) -> None

        """Show tool permission prompt with Allow/Deny buttons.



        Args:

            tool_name: Name of the tool requesting permission.

            args: Tool arguments dict.

            on_respond: Callback called with True (allow) or False (deny).

        """

        self._permission_callback = on_respond

        self._permission_tool_name = tool_name

        self._permission_args = args



        # Hide selection widgets

        self.selection_label.Hide()

        self._clear_selection_widgets()

        self.selection_custom.Hide()



        # Show permission label

        self.permission_label.SetLabel(

            "Tool '{}' requests permission to execute:".format(tool_name)

        )

        self.permission_label.SetFont(

            wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)

        )

        self.permission_label.Show()



        # Format args display

        args_lines = []  # type: List[str]

        if args:

            for key, value in args.items():

                val_str = str(value)

                if len(val_str) > 100:

                    val_str = val_str[:97] + "..."

                args_lines.append("  {}: {}".format(key, val_str))

        args_text = "\n".join(args_lines) if args_lines else "  (no arguments)"

        self.permission_args_text.SetLabel(args_text)

        self.permission_args_text.Show()



        # Clean up existing permission buttons

        self._clear_permission_buttons()



        # Create button panel

        btn_panel = wx.Panel(self)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)



        # Allow button (green)

        self.permission_btn_allow = wx.Button(btn_panel, label="Allow")

        self.permission_btn_allow.SetBackgroundColour(wx.Colour("#22C55E"))

        self.permission_btn_allow.SetForegroundColour(wx.WHITE)

        self.permission_btn_allow.Bind(wx.EVT_BUTTON, lambda e: self._on_permission_respond(True))

        btn_sizer.Add(self.permission_btn_allow, 0, wx.RIGHT, 8)



        # Deny button (red)

        self.permission_btn_deny = wx.Button(btn_panel, label="Deny")

        self.permission_btn_deny.SetBackgroundColour(wx.Colour("#EF4444"))

        self.permission_btn_deny.SetForegroundColour(wx.WHITE)

        self.permission_btn_deny.Bind(wx.EVT_BUTTON, lambda e: self._on_permission_respond(False))

        btn_sizer.Add(self.permission_btn_deny, 0, wx.RIGHT, 8)



        # Allow All button (blue)

        self.permission_btn_allow_all = wx.Button(btn_panel, label="Allow All (Session)")

        self.permission_btn_allow_all.SetBackgroundColour(wx.Colour("#3B82F6"))

        self.permission_btn_allow_all.SetForegroundColour(wx.WHITE)

        self.permission_btn_allow_all.Bind(

            wx.EVT_BUTTON, lambda e: self._on_permission_respond(True, allow_all=True)

        )

        btn_sizer.Add(self.permission_btn_allow_all, 0)



        btn_panel.SetSizer(btn_sizer)



        # Add btn_panel to sizer (remove old one first if exists)

        if hasattr(self, "_permission_btn_panel") and self._permission_btn_panel:

            self.sizer.Detach(self._permission_btn_panel)

            self._permission_btn_panel.Destroy()

        self._permission_btn_panel = btn_panel

        self.sizer.Insert(2, btn_panel, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, PADDING)



        self.Show()

        self.GetParent().Layout()



    def hide_tool_permission(self):

        # type: () -> None

        """Hide the tool permission panel."""

        self.Hide()

        self.permission_label.Hide()

        self.permission_args_text.Hide()

        self._clear_permission_buttons()

        if self._permission_btn_panel is not None:

            self.sizer.Detach(self._permission_btn_panel)

            self._permission_btn_panel.Destroy()

            self._permission_btn_panel = None

        self.GetParent().Layout()



    def _clear_permission_buttons(self):

        # type: () -> None

        """Clear existing permission buttons from the sizer."""

        if self.permission_btn_allow is not None:

            self.permission_btn_allow.Destroy()

            self.permission_btn_allow = None

        if self.permission_btn_deny is not None:

            self.permission_btn_deny.Destroy()

            self.permission_btn_deny = None

        if self.permission_btn_allow_all is not None:

            self.permission_btn_allow_all.Destroy()

            self.permission_btn_allow_all = None



    def _on_permission_respond(self, allowed, allow_all=False):

        # type: (bool, bool) -> None

        """Handle user permission response.



        Args:

            allowed: True if user allowed, False if denied.

            allow_all: True if user clicked "Allow All (Session)".

        """

        if allow_all:

            self._allow_all_session = True



        if self._permission_callback:

            self._permission_callback(allowed)



        self.hide_tool_permission()



# ---------------------------------------------------------------------------

# Model & Agent Selector Bar

# ---------------------------------------------------------------------------



class ModelAgentBar(wx.Panel):

    """Horizontal bar with agent and model selector dropdowns."""



    def __init__(

        self,

        parent,

        agents=None,

        models=None,

        on_agent_change=None,

        on_model_change=None,

    ):

        # type: (wx.Window, Optional[List[str]], Optional[List[str]], Optional[Callable], Optional[Callable]) -> None

        super(ModelAgentBar, self).__init__(parent, style=wx.BORDER_NONE)

        self.SetBackgroundColour(_THEME["bg_primary"])



        self.on_agent_change = on_agent_change

        self.on_model_change = on_model_change



        # Create horizontal sizer with padding

        sizer = wx.BoxSizer(wx.HORIZONTAL)



        # --- Agent Selector ---

        agent_label = wx.StaticText(self, label="Agent:")

        agent_label.SetFont(

            wx.Font(

                10,

                wx.FONTFAMILY_DEFAULT,

                wx.FONTSTYLE_NORMAL,

                wx.FONTWEIGHT_NORMAL,

                False,

                FONT_FAMILY,

            )

        )

        agent_label.SetForegroundColour(wx.Colour("#6E6E73"))



        self.agent_combo = wx.ComboBox(

            self,

            style=wx.CB_READONLY | wx.CB_DROPDOWN,

            size=(140, -1),

        )

        self.agent_combo.SetFont(

            wx.Font(

                10,

                wx.FONTFAMILY_DEFAULT,

                wx.FONTSTYLE_NORMAL,

                wx.FONTWEIGHT_NORMAL,

                False,

                FONT_FAMILY,

            )

        )

        self.agent_combo.Bind(wx.EVT_COMBOBOX, self._on_agent_selected)



        # --- Model Selector ---

        model_label = wx.StaticText(self, label="Model:")

        model_label.SetFont(

            wx.Font(

                10,

                wx.FONTFAMILY_DEFAULT,

                wx.FONTSTYLE_NORMAL,

                wx.FONTWEIGHT_NORMAL,

                False,

                FONT_FAMILY,

            )

        )

        model_label.SetForegroundColour(wx.Colour("#6E6E73"))



        self.model_combo = wx.ComboBox(

            self,

            style=wx.CB_READONLY | wx.CB_DROPDOWN,

            size=(200, -1),

        )

        self.model_combo.SetFont(

            wx.Font(

                10,

                wx.FONTFAMILY_DEFAULT,

                wx.FONTSTYLE_NORMAL,

                wx.FONTWEIGHT_NORMAL,

                False,

                FONT_FAMILY,

            )

        )

        self.model_combo.Bind(wx.EVT_COMBOBOX, self._on_model_selected)



        # Add to sizer with spacing

        sizer.Add(agent_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, PADDING)

        sizer.Add(self.agent_combo, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)

        sizer.Add(model_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, PADDING)

        sizer.Add(self.model_combo, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)

        sizer.AddStretchSpacer()



        self.SetSizer(sizer)



        # Populate dropdowns

        if agents:

            self.set_agents(agents)

        if models:

            self.set_models(models)



    def set_agents(self, agents):

        # type: (List[str]) -> None

        """Update the agent dropdown options."""

        self.agent_combo.Clear()

        self.agent_combo.AppendItems(agents)



    def set_models(self, models):

        # type: (List[str]) -> None

        """Update the model dropdown options."""

        self.model_combo.Clear()

        self.model_combo.AppendItems(models)



    def set_selected_agent(self, agent):

        # type: (str) -> None

        """Select the given agent in the dropdown and notify status bar."""

        idx = self.agent_combo.FindString(agent)

        if idx != wx.NOT_FOUND:

            self.agent_combo.SetSelection(idx)

            # Fire callback so status bar (and other listeners) sync immediately.

            # This covers the common pitfall where programmatic selection

            # silently skips the on_agent_change callback, leaving the

            # status bar stuck on an old name.

            if self.on_agent_change:

                self.on_agent_change(agent)

    def enable_agent_selector(self):

        # type: () -> None

        """Enable the agent selector dropdown."""

        self.agent_combo.Enable()



    def disable_agent_selector(self):

        # type: () -> None

        """Disable the agent selector dropdown."""

        self.agent_combo.Disable()



    def enable_model_selector(self):

        # type: () -> None

        """Enable the model selector dropdown."""

        self.model_combo.Enable()



    def disable_model_selector(self):

        # type: () -> None

        """Disable the model selector dropdown."""

        self.model_combo.Disable()



    def set_selected_model(self, model):  # type: (str) -> None

        """Select the given model in the dropdown."""

        idx = self.model_combo.FindString(model)

        if idx != wx.NOT_FOUND:

            self.model_combo.SetSelection(idx)



    def get_selected_agent(self):

        # type: () -> str

        """Get the currently selected agent."""

        return self.agent_combo.GetValue()



    def get_selected_model(self):

        # type: () -> str

        """Get the currently selected model."""

        return self.model_combo.GetValue()



    def _on_agent_selected(self, event):

        # type: (wx.CommandEvent) -> None

        """Handle agent selection change."""

        if self.on_agent_change:

            self.on_agent_change(event.GetString())



    def _on_model_selected(self, event):

        # type: (wx.CommandEvent) -> None

        """Handle model selection change."""

        if self.on_model_change:

            self.on_model_change(event.GetString())



# ---------------------------------------------------------------------------

# Input Panel

# ---------------------------------------------------------------------------



class _CompleterPopup(wx.PopupTransientWindow):

    """Popup dropdown window listing autocomplete suggestions.

    A wx.PopupTransientWindow that hosts a wx.ListBox. Selecting an item
    with the mouse or the keyboard (via the parent InputPanel) fills in the
    typed slash command. PopupTransientWindow automatically handles
    outside-click and focus-loss dismissal, so InputPanel stays focused on
    input handling.

    The popup applies its sizer directly to itself (no intermediate panel):
    calling ``self.SetSizer(...)`` makes the ListBox fill the popup's client
    area, so the dropdown renders its items instead of collapsing to a
    degenerate region. ``sizer.Fit(self)`` sizes the popup from its content.

    Python 3.8.10 compatible.
    """

    def __init__(self, parent, items):
        # type: (wx.Window, List[Suggestion]) -> None
        super(_CompleterPopup, self).__init__(parent, flags=wx.BORDER_SIMPLE)
        self._items = items  # type: List[Suggestion]

        self.SetBackgroundColour(wx.Colour(250, 250, 250))
        self.listbox = wx.ListBox(
            self,
            style=wx.LB_SINGLE | wx.BORDER_NONE,
        )
        self.listbox.SetBackgroundColour(wx.Colour(250, 250, 250))
        for s in items:
            # Distinguish commands from skills so the user knows a bare
            # "/<name>" loads a skill vs. runs a registered command.
            kind_tag = "[skill]" if getattr(s, "kind", "") == "skill" else "[cmd]"
            row = "{}  {}".format(kind_tag, s.display)
            if s.description:
                row += "  —  {}".format(s.description[:60])
            self.listbox.Append(row)
        self.listbox.SetSelection(0)

        # Let the ListBox stretch to fill the popup, and size the popup from
        # its content. A sizer attached directly to the popup (no intermediate
        # panel) is the reliable way to make the child fill the client area.
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.listbox, 1, wx.EXPAND)
        width = 420
        height = min(32 * len(items) + 16, 220)
        self.SetSize((width, height))
        self.SetSizerAndFit(sizer)

        # Click an item to complete the command.
        self.listbox.Bind(wx.EVT_LISTBOX_DCLICK, self._on_dclick)
        self.listbox.Bind(wx.EVT_LEFT_UP, self._on_click)

    def select_index(self, index):
        # type: (int) -> None
        """Highlight the item at the given index (clamped to range)."""
        if not self._items:
            return
        if index < 0:
            index = 0
        if index >= len(self._items):
            index = len(self._items) - 1
        self.listbox.SetSelection(index)

    def get_selected(self):
        # type: () -> Optional[Suggestion]
        """Return the currently selected Suggestion, or None."""
        idx = self.listbox.GetSelection()
        if idx < 0 or idx >= len(self._items):
            return None
        return self._items[idx]

    def _on_dclick(self, event):
        # type: (wx.CommandEvent) -> None
        """Double-click: complete using the clicked item, then close."""
        self._complete_clicked()
        event.Skip()

    def _on_click(self, event):
        # type: (wx.MouseEvent) -> None
        """Single click: complete using the clicked item, then close."""
        self._complete_clicked()

    def _complete_clicked(self):
        # type: () -> None
        selection = self.get_selected()
        if selection is None:
            return
        # Notify parent to apply the completion
        parent = self.GetParent()
        if parent is not None and hasattr(parent, "_ac_items"):
            parent._apply_suggestion()


class InputPanel(wx.Panel):

    """Bottom panel with multi-line input and send/stop buttons."""



    def __init__(

        self,

        parent,

        on_send=None,

        on_stop=None,

        on_search=None,

        on_clear=None,

        get_session_id=None,

        on_scroll_to_bottom=None,

    ):

        # type: (wx.Window, Optional[Callable], Optional[Callable], Optional[Callable], Optional[Callable], Optional[Callable], Optional[Callable]) -> None

        super(InputPanel, self).__init__(parent, style=wx.BORDER_SIMPLE)

        self.SetBackgroundColour(_THEME["bg_primary"])

        self.on_send = on_send

        self.on_stop = on_stop

        self.on_search = on_search

        self.on_clear = on_clear

        self.get_session_id = get_session_id  # type: Optional[Callable[[], Optional[str]]]

        self.on_scroll_to_bottom = on_scroll_to_bottom

        # Command history state (session-specific)

        self._history = []  # type: List[str]

        self._history_idx = -1  # type: int

        self._saved_text = ""  # type: str



        # Multi-line text input

        self.input_ctrl = wx.TextCtrl(

            self,

            style=wx.TE_MULTILINE | wx.TE_PROCESS_ENTER | wx.BORDER_SIMPLE,

        )

        self.input_ctrl.SetFont(

            wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,

                    wx.FONTWEIGHT_NORMAL, False, FONT_FAMILY)

        )

        self.input_ctrl.SetMinSize((-1, 60))

        self.input_ctrl.Bind(wx.EVT_CHAR, self._on_char)



        # Button panel (fixed-width container for stable layout)

        btn_panel = wx.Panel(self, style=wx.BORDER_NONE)

        btn_panel.SetBackgroundColour(_THEME["bg_primary"])

        btn_panel.SetMinSize((90, -1))

        btn_sizer = wx.BoxSizer(wx.VERTICAL)



        # Send button

        self.send_btn = wx.Button(btn_panel, label="Send")

        self.send_btn.SetBackgroundColour(_THEME["accent"])

        self.send_btn.SetForegroundColour(wx.WHITE)

        self.send_btn.Bind(wx.EVT_BUTTON, self._on_send)

        self._add_rich_tooltip(self.send_btn, "发送消息", "Enter 发送，Shift+Enter 换行")



        # Stop button

        self.stop_btn = wx.Button(btn_panel, label="Stop")

        self.stop_btn.SetBackgroundColour(_THEME["disabled"])

        self.stop_btn.SetForegroundColour(wx.WHITE)

        self.stop_btn.Disable()

        self.stop_btn.Bind(wx.EVT_BUTTON, self._on_stop)

        self._add_rich_tooltip(self.stop_btn, "停止执行", "中断当前正在执行的任务")



        # Scroll to bottom button

        self.scroll_btn = wx.Button(btn_panel, label="v", style=wx.BORDER_SIMPLE)

        self.scroll_btn.SetMinSize((-1, 28))

        self.scroll_btn.SetBackgroundColour(wx.Colour(230, 230, 230))

        self.scroll_btn.SetForegroundColour(wx.BLACK)

        self.scroll_btn.SetFont(

            wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)

        )

        self.scroll_btn.SetToolTip("Scroll to bottom")

        if on_scroll_to_bottom:

            self.scroll_btn.Bind(wx.EVT_BUTTON, lambda e: on_scroll_to_bottom())



        btn_sizer.Add(self.send_btn, 1, wx.EXPAND | wx.BOTTOM, 3)

        btn_sizer.Add(self.stop_btn, 1, wx.EXPAND | wx.TOP | wx.BOTTOM, 3)

        btn_sizer.Add(self.scroll_btn, 0, wx.EXPAND | wx.TOP, 3)

        btn_panel.SetSizer(btn_sizer)



        # Main sizer

        sizer = wx.BoxSizer(wx.HORIZONTAL)

        sizer.Add(self.input_ctrl, 1, wx.EXPAND | wx.ALL, PADDING)

        sizer.Add(btn_panel, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, PADDING)

        self.SetSizer(sizer)



        # Bind global keyboard shortcuts

        self.Bind(wx.EVT_CHAR_HOOK, self._on_global_key)

        # --- Slash-command / skill autocomplete ---------------------------------
        # Popup dropdown anchored below the input control; shown when the
        # user is typing a slash command (the current word starts with '/').
        self._ac_popup = None  # type: Optional[_CompleterPopup]
        self._ac_items = []  # type: List[Any]
        self._ac_index = -1  # type: int
        self._suppress_autocomplete = False  # type: bool

        # Rebuild candidates on every text change (cheap: registry + skill dirs)
        self.input_ctrl.Bind(wx.EVT_TEXT, self._on_text_changed)

        # NOTE: We deliberately do NOT bind EVT_SET_FOCUS on the input to
        # close the popup. PopupTransientWindow already dismisses itself on
        # outside click / focus loss, and an EVT_SET_FOCUS handler here would
        # fight with the popup's own focus management and steal the caret
        # (the "no cursor in the input box" symptom).

    def _close_popup(self):
        # type: () -> None
        """Hide the autocomplete popup if visible."""
        if self._ac_popup is not None:
            self._ac_popup.Destroy()
            self._ac_popup = None
        self._ac_items = []
        self._ac_index = -1

    def _show_popup(self, items, anchor_x, anchor_y):
        # type: (List[Any], int, int) -> None
        """Create (or recreate) the popup with the given suggestion items.

        Args:
            items: List of Suggestion objects to display.
            anchor_x: Screen-space X of the popup's top-left corner.
            anchor_y: Screen-space Y of the popup's top-left corner.
        """
        self._close_popup()
        if not items:
            return
        self._ac_items = items
        # Default selection is the first item (matches _CompleterPopup's
        # listbox.SetSelection(0)); a non-negative index lets Enter/Tab
        # complete the highlighted suggestion even before any arrow key.
        self._ac_index = 0
        popup = _CompleterPopup(self, items)
        popup.SetPosition((anchor_x, anchor_y))
        popup.Show()
        self._ac_popup = popup

        # Keep typing focus on the input control so the caret stays visible
        # and further characters keep updating the popup live.
        if self.input_ctrl is not None:
            self.input_ctrl.SetFocus()

    def _update_autocomplete(self):
        # type: () -> None
        """Refresh the autocomplete popup based on the current input text."""
        text = self.input_ctrl.GetValue()
        pos = self.input_ctrl.GetInsertionPoint()
        query = get_query_from_text(text, pos)

        # Not inside a slash word -> hide.
        # Inside a slash word: query may be "" (just typed "/"), in which
        # case show all candidates.
        if query is None:
            self._close_popup()
            return
        candidates = get_candidates(query)
        if not candidates:
            self._close_popup()
            return
        # Compute anchor: bottom-left of the input control, in screen coords.
        anchor_x, anchor_y = self.input_ctrl.ClientToScreen((0, 0))
        anchor_y += self.input_ctrl.GetSize().GetHeight()
        self._show_popup(candidates, anchor_x, anchor_y)

    def _apply_suggestion(self):
        # type: () -> None
        """Replace the in-progress slash word with the selected suggestion."""
        if self._ac_index < 0 or self._ac_index >= len(self._ac_items):
            return
        suggestion = self._ac_items[self._ac_index]
        text = self.input_ctrl.GetValue()
        pos = self.input_ctrl.GetInsertionPoint()

        # Replace the whole slash token (from its '/' up to the token end)
        # regardless of where the cursor sits inside it.
        token_start = pos
        while token_start > 0 and text[token_start - 1] not in (" ", "\n", "\t"):
            token_start -= 1
        token_end = pos
        while token_end < len(text) and text[token_end] not in (" ", "\n", "\t"):
            token_end += 1

        if token_start >= len(text) or not text[token_start:token_start + 1] == "/":
            # Not a slash token — nothing to complete.
            self._close_popup()
            return
        new_text = text[:token_start] + suggestion.label + " " + text[token_end:]
        self.input_ctrl.SetValue(new_text)
        self.input_ctrl.SetInsertionPoint(token_start + len(suggestion.label) + 1)
        self._close_popup()

    def _on_text_changed(self, event):
        # type: (wx.CommandEvent) -> None
        """Rebuild autocomplete candidates whenever the text changes."""
        if not self._suppress_autocomplete:
            self._update_autocomplete()
        event.Skip()

    def _popup_select_next(self):
        # type: () -> None
        """Move the popup selection down (wrap around)."""
        if not self._ac_items:
            return
        self._ac_index = (self._ac_index + 1) % len(self._ac_items)
        if self._ac_popup is not None:
            self._ac_popup.select_index(self._ac_index)

    def _popup_select_prev(self):
        # type: () -> None
        """Move the popup selection up (wrap around)."""
        if not self._ac_items:
            return
        self._ac_index = (self._ac_index - 1) % len(self._ac_items)
        if self._ac_popup is not None:
            self._ac_popup.select_index(self._ac_index)

    def _popup_is_open(self):
        # type: () -> bool
        """Return True when the autocomplete popup is currently visible."""
        return self._ac_popup is not None and self._ac_popup.IsShown()



    def _add_rich_tooltip(self, control, title, message):

        # type: (wx.Window, str, str) -> None

        """Add a tooltip to a control."""

        # Use wx.ToolTip for hover tooltips (RichToolTip is for explicit notifications)

        control.SetToolTip(wx.ToolTip("{}\n{}".format(title, message)))



    def load_history(self, session_id):

        # type: (str) -> None

        """Load command history for the given session.



        Fetches history from the persistent store and resets navigation state.

        """

        from berserker.session.history import command_history



        self._history = command_history.get(session_id)

        self._history_idx = -1

        self._saved_text = ""



    def _on_global_key(self, event):

        # type: (wx.KeyEvent) -> None

        """Handle global keyboard shortcuts."""

        keycode = event.GetKeyCode()



        # Ctrl+K: Open search

        if keycode == ord("K") and event.ControlDown():

            if self.on_search:

                self.on_search()

            return



        # Ctrl+L: Clear messages

        if keycode == ord("L") and event.ControlDown():

            if self.on_clear:

                self.on_clear()

            return



        event.Skip()



    def _on_char(self, event):

        # type: (wx.KeyEvent) -> None

        """Handle key events: Enter=send, Shift+Enter=newline, Up/Down=history."""

        keycode = event.GetKeyCode()



        # Autocomplete popup key routing (highest priority while open)
        if self._popup_is_open():
            if keycode == wx.WXK_ESCAPE:
                self._close_popup()
                return  # Consume
            elif keycode == wx.WXK_DOWN:
                self._popup_select_next()
                return  # Consume
            elif keycode == wx.WXK_UP:
                self._popup_select_prev()
                return  # Consume
            elif keycode == wx.WXK_TAB:
                self._apply_suggestion()
                return  # Consume
            elif keycode == wx.WXK_RETURN and not event.ShiftDown():
                # Enter while popup open: complete the selected suggestion,
                # do NOT send the message.
                self._apply_suggestion()
                return  # Consume

        if keycode == wx.WXK_RETURN:

            if event.ShiftDown():

                # Shift+Enter: insert newline

                pos = self.input_ctrl.GetInsertionPoint()

                current = self.input_ctrl.GetValue()

                self.input_ctrl.SetValue(current[:pos] + "\n" + current[pos:])

                self.input_ctrl.SetInsertionPoint(pos + 1)

            else:

                # Enter: send message

                self._send_message()

                return  # Don't propagate

        elif keycode == wx.WXK_UP:

            self._navigate_history_up()

            return  # Consume the event

        elif keycode == wx.WXK_DOWN:

            self._navigate_history_down()

            return  # Consume the event

        else:

            event.Skip()



    def _navigate_history_up(self):

        # type: () -> None

        """Navigate to previous (older) command in history."""

        if not self._history:

            return

        if self._history_idx < 0:

            # First up press: save current text and go to most recent

            self._saved_text = self.input_ctrl.GetValue()

            self._history_idx = len(self._history) - 1

        elif self._history_idx > 0:

            self._history_idx -= 1

        else:

            return  # Already at oldest

        self._suppress_autocomplete = True
        self.input_ctrl.SetValue(self._history[self._history_idx])
        self._suppress_autocomplete = False
        self._close_popup()

        self.input_ctrl.SetInsertionPointEnd()



    def _navigate_history_down(self):

        # type: () -> None

        """Navigate to next (newer) command in history."""

        if self._history_idx < 0:

            return  # Not navigating history

        self._history_idx += 1

        if self._history_idx >= len(self._history):

            # Past the end: restore saved text

            self._history_idx = -1

            self._suppress_autocomplete = True
            self.input_ctrl.SetValue(self._saved_text)
            self._suppress_autocomplete = False
        else:
            self._suppress_autocomplete = True
            self.input_ctrl.SetValue(self._history[self._history_idx])
            self._suppress_autocomplete = False
        self._close_popup()

        self.input_ctrl.SetInsertionPointEnd()



    def _on_send(self, event):

        # type: (wx.CommandEvent) -> None

        """Handle send button click."""

        self._send_message()



    def _send_message(self):

        # type: () -> None

        """Send the current input."""

        # Close any open autocomplete popup before sending
        self._close_popup()

        text = self.input_ctrl.GetValue().strip()

        if text and self.on_send:

            # Save to history

            self._history.append(text)

            self._history_idx = -1

            self._saved_text = ""

            # Persist to session history if session_id is available

            if self.get_session_id:

                session_id = self.get_session_id()

                if session_id:

                    from berserker.session.history import command_history



                    command_history.add(session_id, text)

            self.on_send(text)

            self.input_ctrl.Clear()



    def _on_stop(self, event):

        # type: (wx.CommandEvent) -> None

        """Handle stop button click."""

        if self.on_stop:

            self.on_stop()



    def set_send_enabled(self, enabled):

        # type: (bool) -> None

        """Enable/disable send button and input control."""

        self.send_btn.Enable(enabled)

        self.input_ctrl.Enable(enabled)



    def set_stop_enabled(self, enabled):  # type: (bool) -> None

        """Enable/disable stop button."""

        if enabled:

            self.stop_btn.SetBackgroundColour(_THEME["danger"])

            self.stop_btn.Enable()

        else:

            self.stop_btn.SetBackgroundColour(_THEME["disabled"])

            self.stop_btn.Disable()



    def get_value(self):

        # type: () -> str

        """Get current input text."""

        return self.input_ctrl.GetValue()



    def set_value(self, text):

        # type: (str) -> None

        """Set input text."""

        self.input_ctrl.SetValue(text)



# ---------------------------------------------------------------------------

# Main Frame

# ---------------------------------------------------------------------------



class PyBerserkerFrame(wx.Frame):

    """Main wxPython frame for berserker GUI with enhanced features."""



    def __init__(

        self, title="berserker", on_send=None, on_stop=None, workspace=None, show_welcome=True

    ):

        # type: (str, Optional[Callable], Optional[Callable], Optional[str], bool) -> None

        super(PyBerserkerFrame, self).__init__(

            None,

            title=title,

            size=(1050, 600),

            style=wx.DEFAULT_FRAME_STYLE,

        )



        # Set window + taskbar icon from assets (works in source and PyInstaller modes)

        if getattr(sys, 'frozen', False):

            # PyInstaller onefile: assets are extracted to _MEIPASS/berserker/assets/

            base_path = os.path.join(sys._MEIPASS, 'berserker')

        else:

            # Source mode: relative to this file (gui/main.py -> berserker/)

            base_path = os.path.join(os.path.dirname(__file__), '..')

        # Use IconBundle for multi-size icon support (title bar, taskbar, Alt+Tab)

        icon_loaded = False

        # Method 1: load ICO file directly (all sizes, native Windows format)

        icon_path_ico = os.path.join(base_path, 'assets', 'logo.ico')

        if os.path.exists(icon_path_ico):

            try:

                self.SetIcons(wx.IconBundle(icon_path_ico, wx.BITMAP_TYPE_ICO))

                icon_loaded = True

            except Exception:

                logger.debug('Failed to load ICO bundle: %s', icon_path_ico)

        # Method 2: fallback to individual PNGs

        if not icon_loaded:

            bundle = wx.IconBundle()

            for size_name in ('16', '32', '48', '64', '128', '256'):

                png_path = os.path.join(base_path, 'assets', 'logo-%s.png' % size_name)

                if os.path.exists(png_path):

                    try:

                        bundle.AddIcon(wx.Icon(png_path, wx.BITMAP_TYPE_PNG))

                        icon_loaded = True

                    except Exception:

                        logger.debug('Failed to load PNG icon: %s', png_path)

            if icon_loaded:

                self.SetIcons(bundle)

        self.on_send = on_send

        self.on_stop = on_stop

        self._workspace = workspace or os.getcwd()

        self._is_processing = False  # Track if agent is running

        self._session_id = None  # type: Optional[str]



        # Callback for workspace changes (set by app.py to reset global state)

        self._on_workspace_change = None  # type: Optional[Callable]



        # Create main panel with background color

        self.main_panel = wx.Panel(self)

        main_panel = self.main_panel

        main_panel.SetBackgroundColour(_THEME["bg_primary"])



        # Frame sizer to manage main_panel size

        frame_sizer = wx.BoxSizer(wx.VERTICAL)

        frame_sizer.Add(main_panel, 1, wx.EXPAND)

        self.SetSizer(frame_sizer)



        # Splitter window: sidebar (left) + main content (right)

        self.splitter = wx.SplitterWindow(main_panel, style=wx.SP_LIVE_UPDATE | wx.SP_NOBORDER)

        self.splitter.SetBackgroundColour(_THEME["bg_primary"])



        # --- Left side: Sidebar placeholder panel ---

        self.sidebar_panel = wx.Panel(self.splitter)

        self.sidebar_panel.SetMinSize((100, -1))

        self.sidebar_panel.SetBackgroundColour(wx.Colour("#E8E8ED"))

        sidebar_sizer = wx.BoxSizer(wx.VERTICAL)

        sidebar_label = wx.StaticText(self.sidebar_panel, label="Sessions")

        sidebar_label.SetFont(

            wx.Font(

                14,

                wx.FONTFAMILY_DEFAULT,

                wx.FONTSTYLE_NORMAL,

                wx.FONTWEIGHT_BOLD,

                False,

                FONT_FAMILY,

            )

        )

        sidebar_sizer.Add(sidebar_label, 0, wx.ALL, PADDING)

        self.sidebar_panel.SetSizer(sidebar_sizer)



        # --- Right side: Inner splitter (main content + agent status) ---

        self.inner_splitter = wx.SplitterWindow(self.splitter, style=wx.SP_LIVE_UPDATE | wx.SP_NOBORDER)

        self.inner_splitter.SetBackgroundColour(_THEME["bg_primary"])



        # Main content panel (left of inner splitter)

        self.main_content_panel = wx.Panel(self.inner_splitter)

        self.main_content_panel.SetBackgroundColour(_THEME["bg_primary"])



        # Agent status panel (right of inner splitter)

        self.agent_status_panel = AgentStatusPanel(self.inner_splitter)

        # Vertical sizer for main content (preserves existing layout)

        content_sizer = wx.BoxSizer(wx.VERTICAL)



        # InfoBar for temporary notifications

        self.info_bar = wx.InfoBar(self.main_content_panel)

        self.info_bar.Hide()

        content_sizer.Add(self.info_bar, 0, wx.EXPAND)



        # Workspace toolbar with ArtProvider icons

        self.toolbar = self.CreateToolBar()

        self._update_workspace_toolbar()



        # Message display panel (top, expands)

        self.message_display = MessageDisplayPanel(self.main_content_panel)

        content_sizer.Add(self.message_display, 7, wx.EXPAND)



        # Search bar (initially hidden)
        self.search_bar = SearchBarPanel(
            self.main_content_panel, on_search=self._on_search, on_close=self._on_search_close
        )
        self.search_bar.Hide()
        content_sizer.Add(self.search_bar, 0, wx.EXPAND)
        self._search_timer = None  # type: Optional[wx.Timer]  # debounces search input


        # Dynamic widget panel (middle, hidden when empty)

        self.dynamic_widget = DynamicWidgetPanel(self.main_content_panel)

        content_sizer.Add(self.dynamic_widget, 0, wx.EXPAND)



        # Input panel (bottom, fixed height) - includes scroll-to-bottom button

        self.input_panel = InputPanel(

            self.main_content_panel,

            on_send=self._on_send,

            on_stop=self._on_stop,

            on_search=self._show_search,

            on_clear=self._clear_messages,

            get_session_id=lambda: self._session_id,

            on_scroll_to_bottom=self._on_scroll_to_bottom_click,

        )

        content_sizer.Add(self.input_panel, 1, wx.EXPAND)

        # Model & Agent selector bar (below input panel)

        self.model_agent_bar = ModelAgentBar(

            self.main_content_panel,

            on_agent_change=self._on_agent_change,

            on_model_change=self._on_model_change,

        )

        content_sizer.Add(self.model_agent_bar, 0, wx.EXPAND)



        self.main_content_panel.SetSizer(content_sizer)



        # Split inner splitter vertically (main content left, agent status right)

        # Both panes split proportionally: gravity 0.78 means main content gets 78%

        # of the available width on resize, agent status gets 22%.

        self.inner_splitter.SplitVertically(self.main_content_panel, self.agent_status_panel)

        self.inner_splitter.SetMinimumPaneSize(50)

        self.inner_splitter.SetSashGravity(1.0 - AGENT_STATUS_RATIO / (1.0 - SIDEBAR_RATIO))

        # Example: SIDEBAR_RATIO=0.18, AGENT_STATUS_RATIO=0.30

        # Remaining for inner splitter after sidebar = 0.82

        # Inner gravity = 1.0 - 0.30/0.82 = 1.0 - 0.366 ≈ 0.634



        # Split outer splitter vertically (sidebar left, inner splitter right)

        self.splitter.SplitVertically(self.sidebar_panel, self.inner_splitter)

        self.splitter.SetMinimumPaneSize(80)

        self.splitter.SetSashGravity(SIDEBAR_RATIO)



        # Initialize sash positions proportionally after window layout is finalized

        wx.CallAfter(self._init_sash_positions)

        # Vertical sizer containing the splitter

        main_sizer = wx.BoxSizer(wx.VERTICAL)

        main_sizer.Add(self.splitter, 1, wx.EXPAND)

        main_panel.SetSizer(main_sizer)



        # Status bar with 4 fields: indicator, status, workspace, agent info

        self.CreateStatusBar(4)

        self.SetStatusWidths(

            [24, -2, -1, 200]

        )  # Indicator (fixed), status (flex), workspace (flex), agent (fixed)

        self.SetStatusText("Ready", 1)

        self.SetStatusText("Workspace: {}".format(self._workspace), 2)

        self.SetStatusText("Agent: ...", 3)  # Overwritten by _on_agent_change callback after ModelAgentBar initialization



        # Activity indicator in status bar field 0

        self.activity_indicator = wx.ActivityIndicator(self.StatusBar)

        self.activity_indicator.Hide()



        # Bind frame resize to ensure layout propagates to all panels

        self.Bind(wx.EVT_SIZE, self._on_frame_size)



        # Center on screen

        self.Centre()



        # Show welcome message with keyboard shortcuts (if enabled)

        if show_welcome:

            wx.CallAfter(self._show_welcome)



    def _on_frame_size(self, event):

        # type: (wx.SizeEvent) -> None

        """Handle frame resize — ensure all panels lay out correctly."""

        event.Skip()

        wx.CallAfter(self.Layout)



    def _update_workspace_toolbar(self):

        # type: () -> None

        """Update toolbar with workspace controls using Twemoji icons."""

        self.toolbar.ClearTools()



        # Load workspace icon from twemoji

        workspace_bmp = self._load_twemoji("1f4c1")  # folder

        clear_bmp = self._load_twemoji("1f5d1")     # trash/clear



        # Add workspace button

        change_id = wx.NewIdRef()

        self.toolbar.AddTool(

            change_id,

            "Change Workspace",

            workspace_bmp or wx.ArtProvider.GetBitmap(wx.ART_FOLDER, wx.ART_TOOLBAR, (16, 16)),

            shortHelp="Change workspace directory",

        )

        self.toolbar.Bind(wx.EVT_TOOL, self._on_change_workspace, id=change_id)



        self.toolbar.AddSeparator()



        # Add clear messages button

        clear_id = wx.NewIdRef()

        self.toolbar.AddTool(

            clear_id,

            "Clear",

            clear_bmp or wx.ArtProvider.GetBitmap(wx.ART_DELETE, wx.ART_TOOLBAR, (16, 16)),

            shortHelp="Clear all messages (Ctrl+L)",

        )

        self.toolbar.Bind(wx.EVT_TOOL, lambda e: self._clear_messages(), id=clear_id)



        self.toolbar.Realize()



    @staticmethod

    def _load_twemoji(code):

        # type: (str) -> Optional[wx.Bitmap]

        """Load a twemoji PNG and scale to toolbar size."""

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



    def _on_change_workspace(self, event):

        # type: (wx.CommandEvent) -> None

        """Handle change workspace button click."""

        new_workspace = select_workspace(self, self._workspace)

        if new_workspace:

            self.set_workspace(new_workspace)



    # -----------------------------------------------------------------------

    # Session management

    # -----------------------------------------------------------------------



    @property

    def session_id(self):

        # type: () -> Optional[str]

        """Get current session ID."""

        return self._session_id



    @session_id.setter

    def session_id(self, value):

        # type: (Optional[str]) -> None

        """Set current session ID and reload command history."""

        self._session_id = value

        if value:

            self.input_panel.load_history(value)



    def _init_sash_positions(self):

        # type: () -> None

        """Set initial sash positions proportional to current window width.



        Called once after window is shown via wx.CallAfter.

        SetSashGravity handles subsequent resizes proportionally.

        """

        total_width = self.GetClientSize().GetWidth()

        if total_width <= 0:

            return



        # Sidebar: SIDEBAR_RATIO of total width

        sidebar_width = max(80, int(total_width * SIDEBAR_RATIO))

        self.splitter.SetSashPosition(sidebar_width)



        # Agent status: AGENT_STATUS_RATIO of total width

        agent_status_width = max(50, int(total_width * AGENT_STATUS_RATIO))

        remaining = total_width - sidebar_width

        main_content_width = max(200, remaining - agent_status_width)

        self.inner_splitter.SetSashPosition(main_content_width)



    # -----------------------------------------------------------------------

    # Sidebar management

    # -----------------------------------------------------------------------

    def set_workspace_change_callback(self, callback):

        # type: (Callable) -> None

        """Set callback to be called when workspace changes.



        Used by app.py to reset global session state (_current_session_id, _session_context).



        Args:

            callback: Function to call with no arguments.

        """

        self._on_workspace_change = callback



    def set_sidebar(self, sidebar_panel):  # type: (wx.Panel) -> None

        """Replace the placeholder sidebar with the actual SidebarPanel.



        Uses SplitterWindow.ReplaceWindow() to swap the old sidebar

        with the new one, then resets the sash position.



        Args:

            sidebar_panel: The actual SidebarPanel to display.

        """

        # Reparent new sidebar to splitter before replacing

        sidebar_panel.Reparent(self.splitter)



        # Replace old sidebar with new one in the splitter

        old_sidebar = self.sidebar_panel

        self.splitter.ReplaceWindow(old_sidebar, sidebar_panel)

        old_sidebar.Destroy()



        # Update reference

        self.sidebar_panel = sidebar_panel



        # Reset sash position to proportional width

        total_width = self.GetClientSize().GetWidth()

        sidebar_width = max(80, int(total_width * SIDEBAR_RATIO))

        self.splitter.SetSashPosition(sidebar_width)

        self.splitter.Layout()

    # -----------------------------------------------------------------------

    # Workspace management

    # -----------------------------------------------------------------------



    def get_workspace(self):

        # type: () -> str

        """Get current workspace path."""

        return self._workspace



    def set_workspace(self, path):

        # type: (str) -> None

        """Set workspace path and update UI + global workspace module."""

        abs_path = os.path.abspath(path)

        self._workspace = abs_path



        # Sync to global workspace module so all tools use the new path

        from berserker.workspace import set_workspace as _set_global_workspace



        _set_global_workspace(abs_path)



        # Refresh project-scoped commands for new workspace

        from berserker.command.registry import command_registry

        command_registry.refresh_project_commands(abs_path)



        # Update UI

        self.SetStatusText("Workspace: {}".format(self._workspace), 2)

        self.SetTitle("berserker — {}".format(os.path.basename(self._workspace)))



        # Get new workspace ID

        from berserker.workspace.manager import WorkspaceManager



        workspace_id = WorkspaceManager().get_or_create(abs_path)



        # Update session manager's project_id FIRST so sidebar refresh uses correct project_id

        from berserker.session.manager import session_manager



        session_manager.update_project_id(abs_path)



        # Clear message display - old workspace messages are no longer relevant

        self._clear_messages()



        # Reset controller session state (session_id, session_context, execution state)

        if hasattr(self, "controller") and self.controller is not None:

            self.controller.reset_session_state()

            self.controller.workspace_id = workspace_id



        # Disable input until a new session is selected

        self.set_send_enabled(False)



        # Update status bar to indicate no session selected

        self.SetStatusText("No Session Selected", 1)



        # Notify sidebar panel of workspace change (refreshes sessions using new project_id)

        if hasattr(self, "sidebar_panel") and self.sidebar_panel is not None:

            self.sidebar_panel.set_workspace(workspace_id)



        # Remember workspace for next GUI launch

        from berserker.gui.state import set_last_workspace



        set_last_workspace(abs_path)



        # Notify app.py to reset global session state

        if self._on_workspace_change is not None:

            self._on_workspace_change()



    def _on_scroll_to_bottom_click(self):

        # type: () -> None

        """Scroll message display to bottom."""

        self.message_display.chat_list.scroll_to_bottom()


    def _on_send(self, text):  # type: (str) -> None

        """Handle send event from input panel."""

        if self.on_send:

            self.on_send(text)



    def _on_stop(self):

        # type: () -> None

        """Handle stop event from input panel."""

        if self.on_stop:

            self.on_stop()



    # -----------------------------------------------------------------------

    # Thread-safe UI update methods

    # -----------------------------------------------------------------------

    def add_user_message(self, text, defer_layout=False, timestamp=None):
        # type: (str, bool, Optional[str]) -> None
        """Add a user message (thread-safe).

        Args:
            text: Message text content.
            defer_layout: If True, call directly (caller must be on the main
                thread — used for batched history loading).
            timestamp: Optional display timestamp string (e.g. "HH:MM:SS").
        """
        if defer_layout:
            # Direct call for batch loading (must be on main thread)
            self._add_user_message(text, defer_layout=True, timestamp=timestamp)
        else:
            wx.CallAfter(self._add_user_message, text, defer_layout=False, timestamp=timestamp)

    def _add_user_message(self, text, defer_layout=False, timestamp=None):
        # type: (str, bool, Optional[str]) -> None
        self.message_display.add_user_message(text, defer_layout=defer_layout, timestamp=timestamp)

    def add_assistant_message(self, text, defer_layout=False, timestamp=None):
        # type: (str, bool, Optional[str]) -> None
        """Add an assistant message (thread-safe).

        Args:
            text: Message text content.
            defer_layout: If True, call directly (caller must be on the main
                thread — used for batched history loading).
            timestamp: Optional display timestamp string (e.g. "HH:MM:SS").
        """
        if defer_layout:
            self._add_assistant_message(text, defer_layout=True, timestamp=timestamp)
        else:
            wx.CallAfter(self._add_assistant_message, text, defer_layout=False, timestamp=timestamp)

    def _add_assistant_message(self, text, defer_layout=False, timestamp=None):
        # type: (str, bool, Optional[str]) -> None
        self.message_display.add_assistant_message(text, defer_layout=defer_layout, timestamp=timestamp)

    def add_tool_call(self, tool_name, result, defer_layout=False, timestamp=None):
        # type: (str, Dict[str, Any], bool, Optional[str]) -> None
        """Add a tool call display (thread-safe).

        Args:
            tool_name: Name of the tool that was called.
            result: Tool execution result dict.
            defer_layout: If True, call directly (caller must be on the main
                thread — used for batched history loading).
            timestamp: Optional display timestamp string (e.g. "HH:MM:SS").
        """
        if defer_layout:
            self._add_tool_call(tool_name, result, defer_layout=True, timestamp=timestamp)
        else:
            wx.CallAfter(self._add_tool_call, tool_name, result, defer_layout=False, timestamp=timestamp)

    def _add_tool_call(self, tool_name, result, defer_layout=False, timestamp=None):
        # type: (str, Dict[str, Any], bool, Optional[str]) -> None
        self.message_display.add_tool_call(tool_name, result, defer_layout=defer_layout, timestamp=timestamp)

    def set_messages(self, items, scroll_to_bottom=False):
        # type: (list, bool) -> None
        """Replace the message display with the given items (main thread only).

        Args:
            items: List of ChatMessageData items.
            scroll_to_bottom: If True, scroll so the newest message is visible.
        """
        self.message_display.set_messages(items, scroll_to_bottom=scroll_to_bottom)

    def insert_messages_at_front(self, items):
        # type: (list) -> None
        """Insert older messages at the front, preserving the viewport (main thread only).

        Args:
            items: List of ChatMessageData items to prepend.
        """
        self.message_display.insert_messages_at_front(items)

    def add_load_earlier_button(self, count, callback=None):
        # type: (int, Optional[Callable[[], None]]) -> None

        """Add a 'Load earlier messages' link (main thread only)."""

        self.message_display.add_load_earlier_button(count, callback)



    def remove_load_earlier_button(self):

        # type: () -> None

        """Remove the 'Load earlier messages' link (main thread only)."""

        self.message_display.remove_load_earlier_button()



    def show_selection(self, question, options, multiple=False, allow_custom=False, on_select=None):

        # type: (str, List[Dict[str, str]], bool, bool, Optional[Callable]) -> None

        """Show selection widget (thread-safe)."""

        wx.CallAfter(self._show_selection, question, options, multiple, allow_custom, on_select)



    def _show_selection(self, question, options, multiple, allow_custom, on_select):

        # type: (str, List[Dict[str, str]], bool, bool, Optional[Callable]) -> None

        self.dynamic_widget.show_selection(question, options, multiple, allow_custom, on_select)



    def hide_dynamic_widget(self):

        # type: () -> None

        """Hide the dynamic widget panel (thread-safe)."""

        wx.CallAfter(self._hide_dynamic_widget)



    def _hide_dynamic_widget(self):

        # type: () -> None

        self.dynamic_widget.hide()



    def show_tool_permission(self, tool_name, args, on_respond=None):

        # type: (str, Dict[str, Any], Optional[Callable]) -> None

        """Show tool permission prompt (thread-safe)."""

        wx.CallAfter(self._show_tool_permission, tool_name, args, on_respond)



    def _show_tool_permission(self, tool_name, args, on_respond):

        # type: (str, Dict[str, Any], Optional[Callable]) -> None

        self.dynamic_widget.show_tool_permission(tool_name, args, on_respond)



    def hide_tool_permission(self):

        # type: () -> None

        """Hide the tool permission panel (thread-safe)."""

        wx.CallAfter(self._hide_tool_permission)



    def _hide_tool_permission(self):

        # type: () -> None

        self.dynamic_widget.hide_tool_permission()



    def is_allow_all_session(self):

        # type: () -> bool

        """Check if 'Allow All (Session)' is enabled."""

        return self.dynamic_widget._allow_all_session



    def set_status(self, text):

        # type: (str) -> None

        """Update status bar text (thread-safe)."""

        wx.CallAfter(self._set_status, text)



    def _set_status(self, text):

        # type: (str) -> None

        self.SetStatusText(text, 1)



    def set_send_enabled(self, enabled):

        # type: (bool) -> None

        """Enable/disable send button (thread-safe)."""

        wx.CallAfter(self._set_send_enabled, enabled)



    def _set_send_enabled(self, enabled):

        # type: (bool) -> None

        self.input_panel.set_send_enabled(enabled)



    def set_stop_enabled(self, enabled):

        # type: (bool) -> None

        """Enable/disable stop button (thread-safe)."""

        wx.CallAfter(self._set_stop_enabled, enabled)



    def _set_stop_enabled(self, enabled):

        # type: (bool) -> None

        self.input_panel.set_stop_enabled(enabled)



    def clear_messages(self):

        # type: () -> None

        """Clear all messages (thread-safe)."""

        wx.CallAfter(self._clear_messages)



    def _clear_messages(self):

        # type: () -> None

        self.message_display.clear()



    # -----------------------------------------------------------------------

    # Search functionality

    # -----------------------------------------------------------------------



    def _show_search(self):

        # type: () -> None

        """Show the search bar."""

        self.search_bar.Show()

        self.search_bar.clear()

        self.search_bar.search_ctrl.SetFocus()

        self.Layout()



    def _on_search(self, query):
        # type: (str) -> None
        """Handle search query with debounce.

        The search bar fires on every keystroke; scanning all messages on the
        main thread for each key would freeze the UI on large histories, so the
        actual scan is deferred by 250ms.
        """
        if self._search_timer is not None:
            self._search_timer.Stop()
        self._search_timer = wx.CallLater(250, self._do_search, query)

    def _do_search(self, query):
        # type: (str) -> None
        """Run the actual (debounced) search."""
        self._search_timer = None
        match_count = self.message_display.search_messages(query)

        if query:
            if match_count > 0:
                self.info_bar.ShowMessage(
                    "Found {} match{}".format(match_count, "es" if match_count != 1 else ""),
                    wx.ICON_INFORMATION,
                )
            else:
                self.info_bar.ShowMessage("No matches found", wx.ICON_WARNING)
        else:
            self.info_bar.Dismiss()

    def _on_search_close(self):
        # type: () -> None
        """Close search bar and clear highlights."""
        if self._search_timer is not None:
            self._search_timer.Stop()
            self._search_timer = None
        self.search_bar.Hide()
        self.message_display._clear_search_highlights()
        self.info_bar.Dismiss()
        self.Layout()


    # -----------------------------------------------------------------------

    # Welcome message

    # -----------------------------------------------------------------------



    def _show_welcome(self):

        # type: () -> None

        """Display welcome message with keyboard shortcut hints."""

        welcome_text = (

            "Welcome to berserker!\n\n"

            "Keyboard Shortcuts:\n"

            "  Ctrl+K  - Open search\n"

            "  Ctrl+L  - Clear messages\n"

            "  Enter   - Send message\n"

            "  Shift+Enter - New line\n\n"

            "Right-click on messages to copy or delete."

        )

        self.message_display.add_assistant_message(welcome_text)



    # -----------------------------------------------------------------------

    # Processing state & notifications

    # -----------------------------------------------------------------------

    def set_processing_state(self, is_processing):

        # type: (bool) -> None

        """Toggle processing state - show/hide activity indicator in status bar, enable/disable buttons."""

        self._is_processing = is_processing



        if is_processing:

            # Position activity indicator in status bar field 0

            status_bar = self.StatusBar

            if status_bar:

                rect = status_bar.GetFieldRect(0)

                # Position at left side of field 0, with small padding

                indicator_size = 16

                x = rect.x + 4

                y = rect.y + (rect.height - indicator_size) // 2

                self.activity_indicator.SetPosition((x, y))

                self.activity_indicator.SetSize((indicator_size, indicator_size))

            self.activity_indicator.Show()

            self.activity_indicator.Start()

            self.input_panel.set_send_enabled(False)

            self.input_panel.set_stop_enabled(True)

            self.SetStatusText("Processing...", 1)

        else:

            self.activity_indicator.Stop()

            self.activity_indicator.Hide()

            self.input_panel.set_send_enabled(True)

            self.input_panel.set_stop_enabled(False)

            self.SetStatusText("Ready", 1)



        self.Layout()



    def show_notification(self, title, message):

        # type: (str, str) -> None

        """Show a desktop notification using wx.adv.NotificationMessage."""

        # Use NotificationMessage for non-intrusive notification

        notification = wx.adv.NotificationMessage(

            title=title,

            message=message,

            parent=self,

            flags=wx.ICON_INFORMATION,

        )

        notification.Show()



    def set_agent_status(self, agent_name):

        # type: (str) -> None

        """Update agent status in status bar field 3."""

        self.SetStatusText("Agent: {}".format(agent_name), 3)



    def _on_agent_change(self, agent_name):

        # type: (str) -> None

        """Handle agent selection from dropdown."""

        self.set_agent_status(agent_name)

        # Notify controller if callback is set

        if hasattr(self, "controller") and self.controller:

            self.controller.switch_agent(agent_name)



    def _on_model_change(self, model_name):

        # type: (str) -> None

        """Handle model selection from dropdown."""

        # Update status bar or notify controller

        if hasattr(self, "controller") and self.controller:

            self.controller.switch_model(model_name)



# ---------------------------------------------------------------------------

# Workspace Selection Dialog

# ---------------------------------------------------------------------------

# Workspace Selection Dialog

# ---------------------------------------------------------------------------



class WorkspaceDialog(wx.Dialog):

    """Dialog for selecting or entering workspace path."""



    def __init__(self, parent, default_path=None):

        # type: (wx.Window, Optional[str]) -> None

        super(WorkspaceDialog, self).__init__(

            parent,

            title="Select Workspace",

            size=(500, 200),

            style=wx.DEFAULT_DIALOG_STYLE,

        )



        # Main panel

        panel = wx.Panel(self)

        sizer = wx.BoxSizer(wx.VERTICAL)



        # Instruction text

        instruction = wx.StaticText(

            panel,

            label="Select a project directory to use as the workspace.\n"

            "This is where berserker will read/write files.",

        )

        instruction.Wrap(450)

        sizer.Add(instruction, 0, wx.ALL | wx.EXPAND, PADDING)



        # Path input row

        path_sizer = wx.BoxSizer(wx.HORIZONTAL)



        self.path_ctrl = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)

        if default_path:

            self.path_ctrl.SetValue(default_path)

        else:

            self.path_ctrl.SetValue(os.getcwd())



        browse_btn = wx.Button(panel, label="Browse...")

        browse_btn.Bind(wx.EVT_BUTTON, self._on_browse)



        path_sizer.Add(self.path_ctrl, 1, wx.LEFT | wx.RIGHT | wx.BOTTOM, PADDING)

        path_sizer.Add(browse_btn, 0, wx.RIGHT | wx.BOTTOM, PADDING)



        sizer.Add(path_sizer, 0, wx.EXPAND)



        # Buttons (created as children of panel, not dialog)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        ok_btn = wx.Button(panel, wx.ID_OK, label="OK")

        cancel_btn = wx.Button(panel, wx.ID_CANCEL, label="Cancel")

        btn_sizer.AddStretchSpacer()

        btn_sizer.Add(ok_btn, 0, wx.RIGHT, PADDING)

        btn_sizer.Add(cancel_btn, 0)

        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, PADDING)



        panel.SetSizer(sizer)



        # Bind OK button

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

        self.path_ctrl.Bind(wx.EVT_TEXT_ENTER, lambda e: self.EndModal(wx.ID_OK))



        # Center on parent

        self.Centre()



    def _on_browse(self, event):

        # type: (wx.CommandEvent) -> None

        """Open directory picker dialog."""

        current = self.path_ctrl.GetValue().strip()

        if not current or not os.path.isdir(current):

            current = os.getcwd()



        dlg = wx.DirDialog(

            self,

            message="Select Workspace Directory",

            defaultPath=current,

            style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST,

        )



        if dlg.ShowModal() == wx.ID_OK:

            self.path_ctrl.SetValue(dlg.GetPath())



        dlg.Destroy()



    def _on_ok(self, event):

        # type: (wx.CommandEvent) -> None

        """Validate path before closing."""

        path = self.path_ctrl.GetValue().strip()

        if not path:

            wx.MessageBox("Please enter a workspace path.", "Error", wx.OK | wx.ICON_ERROR)

            return



        if not os.path.isdir(path):

            wx.MessageBox(

                "Directory does not exist:\n{}".format(path),

                "Error",

                wx.OK | wx.ICON_ERROR,

            )

            return



        event.Skip()



    def get_path(self):

        # type: () -> str

        """Get the selected workspace path."""

        return os.path.abspath(self.path_ctrl.GetValue().strip())



# ---------------------------------------------------------------------------

# Application Entry Point

# ---------------------------------------------------------------------------



class PyBerserkerApp(wx.App):

    """wxPython application for berserker."""



    def OnInit(self):

        # type: () -> bool

        """Initialize the application."""

        self.frame = PyBerserkerFrame(title="berserker")

        self.frame.Show()

        return True



def start_gui(on_send=None, on_stop=None):

    # type: (Optional[Callable], Optional[Callable]) -> None

    """Start the berserker GUI.



    Args:

        on_send: Callback(text) called when user sends a message.

        on_stop: Callback() called when user clicks stop.

    """

    app = PyBerserkerApp()

    # Update frame callbacks

    app.frame.on_send = on_send

    app.frame.on_stop = on_stop

    app.MainLoop()



def select_workspace(parent=None, default_path=None):

    # type: (Optional[wx.Window], Optional[str]) -> Optional[str]

    """Show workspace selection dialog.



    Args:

        parent: Parent window for the dialog.

        default_path: Default path to show in the dialog.



    Returns:

        Selected workspace path, or None if cancelled.

    """

    dlg = WorkspaceDialog(parent, default_path)

    if dlg.ShowModal() == wx.ID_OK:

        path = dlg.get_path()

        dlg.Destroy()

        return path

    dlg.Destroy()

    return None



if __name__ == "__main__":

    # Test mode: run with dummy callbacks

    def on_send(text):

        # type: (str) -> None

        print("Sent: {}".format(text))



    def on_stop():

        # type: () -> None

        print("Stopped!")



    start_gui(on_send=on_send, on_stop=on_stop)

