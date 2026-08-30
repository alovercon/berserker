"""Agent status monitor panel for GUI."""

from __future__ import annotations

import wx
import wx.lib.mixins.listctrl as listmix
from typing import Dict, List, Optional, Any

from berserker.agent.monitor import agent_monitor, STATUS_IDLE, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED
from berserker.gui.theme import THEME


class AgentStatusPanel(wx.Panel):
    """Panel displaying real-time agent status."""

    def __init__(self, parent):
        super(AgentStatusPanel, self).__init__(parent)

        self.SetBackgroundColour(THEME["bg_panel"])

        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Header bar
        header_sizer = wx.BoxSizer(wx.HORIZONTAL)

        title = wx.StaticText(self, label="Agent Status")
        title.SetFont(wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD,
                              faceName="Segoe UI"))
        header_sizer.Add(title, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)

        header_sizer.AddStretchSpacer()

        self.clear_btn = wx.Button(self, label="Clear", size=(60, 24))
        self.clear_btn.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.clear_btn.SetToolTip("Remove all entries from the status list")
        self.clear_btn.Bind(wx.EVT_BUTTON, self._on_clear)
        header_sizer.Add(self.clear_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        main_sizer.Add(header_sizer, 0, wx.EXPAND | wx.TOP, 6)

        # List control — 5 columns with better proportions
        self.agent_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.agent_list.InsertColumn(0, "Agent", width=80)
        self.agent_list.InsertColumn(1, "Phase", width=60)
        self.agent_list.InsertColumn(2, "Status", width=70)
        self.agent_list.InsertColumn(3, "Task", width=120)
        self.agent_list.InsertColumn(4, "Tokens", width=80)

        main_sizer.Add(self.agent_list, 1, wx.ALL | wx.EXPAND, 5)

        self.SetSizer(main_sizer)

        self.Bind(wx.EVT_SIZE, self._on_resize)

        # Timer for periodic updates
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        self.timer.Start(1000)

        agent_monitor.add_listener(self.on_agent_update)

        self.populate_list()
        wx.CallAfter(self._adjust_column_widths)

    def _on_clear(self, event):
        agent_monitor.clear_all()
        self.populate_list()

    def _on_resize(self, event):
        event.Skip()
        wx.CallAfter(self._adjust_column_widths)

    def _adjust_column_widths(self):
        if not self.agent_list or not self.agent_list.IsShownOnScreen():
            return
        try:
            total = self.agent_list.GetClientSize().width
            if total <= 0:
                return
            usable = max(total - 25, 40)
            ratios = [0.18, 0.14, 0.16, 0.32, 0.20]  # Agent, Phase, Status, Task, Tokens
            for col, ratio in enumerate(ratios):
                self.agent_list.SetColumnWidth(col, int(usable * ratio))
        except Exception:
            pass

    def on_timer(self, event):
        self.populate_list()

    def on_agent_update(self, agent_id, agent_state):
        try:
            wx.CallAfter(self.populate_list)
        except AssertionError:
            pass  # wx.App already shut down

    def populate_list(self):
        self.agent_list.DeleteAllItems()

        agents = agent_monitor.get_all_agents()
        for i, agent in enumerate(agents):
            self.agent_list.InsertItem(i, agent.name)
            self.agent_list.SetItem(i, 1, agent.phase or "-")

            # Status text (row color provides visual indication)
            self.agent_list.SetItem(i, 2, agent.status)

            # Truncate long task descriptions
            task = agent.task or "-"
            if len(task) > 35:
                task = task[:32] + "..."
            self.agent_list.SetItem(i, 3, task)

            self.agent_list.SetItem(i, 4, "{} iters, {} tok".format(agent.iteration, agent.token_count))

            # Color coding
            if agent.status == STATUS_RUNNING:
                self.agent_list.SetItemBackgroundColour(i, wx.Colour("#E3F2FD"))
            elif agent.status == STATUS_COMPLETED:
                self.agent_list.SetItemBackgroundColour(i, wx.Colour("#E8F5E9"))
            elif agent.status == STATUS_FAILED:
                self.agent_list.SetItemBackgroundColour(i, wx.Colour("#FFEBEE"))

    def destroy(self):
        self.timer.Stop()
        agent_monitor.remove_listener(self.on_agent_update)
        super(AgentStatusPanel, self).Destroy()

    def Destroy(self):
        self.timer.Stop()
        agent_monitor.remove_listener(self.on_agent_update)
        super(AgentStatusPanel, self).Destroy()
