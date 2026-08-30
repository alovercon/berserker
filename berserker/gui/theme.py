"""
berserker.gui.theme — Shared GUI theme tokens.

Centralizes the UI color palette so every GUI module (main, sidebar,
agent_status_panel, chat_listbox) references the same token dictionary
instead of duplicating/wildcasting raw wx.Colour values. Keeping this in a
standalone module avoids the circular import that would occur if sidebar /
agent_status_panel imported _THEME back from main.py (main imports those
panels).

Python 3.8.10 compatible: uses type comments, no | union syntax.
"""

from __future__ import annotations

import wx


# Theme: modern blue accent palette
THEME = {
    "accent": wx.Colour(0, 122, 255),       # #007AFF — iOS blue
    "accent_hover": wx.Colour(0, 102, 230),  # Darker blue for hover
    "danger": wx.Colour(255, 59, 48),        # #FF3B30 — iOS red
    "disabled": wx.Colour(199, 199, 204),    # #C7C7CC — gray

    "bg_primary": wx.Colour(240, 240, 240),  # #F0F0F0 — main bg
    "bg_sidebar": wx.Colour(235, 235, 240),  # #EBEBF0 — sidebar bg
    "bg_card": wx.Colour(255, 255, 255),     # #FFFFFF — card/panel bg
    "bg_hover": wx.Colour(229, 229, 234),    # #E5E5EA — hover/selected surface
    "bg_panel": wx.Colour(245, 245, 247),    # #F5F5F7 — soft panel bg

    "text_primary": wx.Colour(29, 29, 31),   # #1D1D1F
    "text_secondary": wx.Colour(110, 110, 115),  # #6E6E73
    "text_muted": wx.Colour(142, 142, 147),  # #8E8E93

    "separator": wx.Colour(209, 209, 214),   # #D1D1D6
}


def theme_hex(key):
    # type: (str) -> str
    """Return a '#'+RRGGBB string for a THEME token (for wx.Colour("..." ))."""
    colour = THEME[key]
    return "#{:02X}{:02X}{:02X}".format(colour.Red(), colour.Green(), colour.Blue())
