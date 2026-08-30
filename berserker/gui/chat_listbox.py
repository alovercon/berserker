"""
berserker.gui.chat_listbox — Virtualized chat message list using wx.VListBox.

Replaces the old ScrolledPanel + individual wx.Panel bubble architecture with
a high-performance virtualized list that only renders visible items.

Features:
- Virtualized rendering (handles 10,000+ messages with no performance loss)
- Variable-height items via OnMeasureItem
- GraphicsContext anti-aliased rounded bubble backgrounds
- Twemoji SVG emoji for avatars and status indicators
- Message grouping (consecutive same-author messages collapse timestamps)
- Search with highlight
- Pagination support ("Load earlier" link at top)

Python 3.8.10 compatible: uses type comments, no | union syntax.
"""

from __future__ import annotations

import json
import os
import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass
from datetime import datetime

import wx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COLOR_BG_PANEL = wx.Colour(0xF0, 0xF0, 0xF0)

# User message colors — soft blue-green tint that sits harmoniously with the
# iOS-blue accent (keeps the "sender" cue while staying subtle and light).
_COLOR_USER_BUBBLE = wx.Colour(0xDE, 0xF0, 0xFF)       # Soft sky blue
_COLOR_USER_TEXT = wx.Colour(0x1D, 0x1D, 0x1F)

# Assistant message colors
_COLOR_ASST_BUBBLE = wx.Colour(0xFF, 0xFF, 0xFF)        # White
_COLOR_ASST_TEXT = wx.Colour(0x1D, 0x1D, 0x1F)

# Tool call colors — neutral cool gray, distinct from the #F0F0F0 panel bg
_COLOR_TOOL_BUBBLE = wx.Colour(0xEC, 0xEC, 0xF0)        # Cool light gray
_COLOR_TOOL_TEXT = wx.Colour(0x6E, 0x6E, 0x73)

# Grouping / meta colors
_COLOR_TIMESTAMP = wx.Colour(0x8E, 0x8E, 0x93)
_COLOR_SEARCH_HIGHLIGHT = wx.Colour(0xFF, 0xF3, 0xCD)
_COLOR_BUBBLE_BORDER = wx.Colour(0xDF, 0xDF, 0xE6)
_COLOR_LINK = wx.Colour(0x00, 0x7A, 0xFF)  # matches THEME accent

_PADDING = 10           # General padding (slightly roomier)
_BUBBLE_RADIUS = 16      # Corner radius for bubbles
_BUBBLE_MARGIN = 6       # Top/bottom margin between bubbles
_AVATAR_SIZE = 20        # Avatar circle size
_GAP_SAME_GROUP = 3      # Gap between grouped messages
_TIMESTAMP_HEIGHT = 14   # Height reserved for timestamp
# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


def _html_escape(s):
    # type: (str) -> str
    """Minimal HTML escape for raw text fallback."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass
class ChatMessageData:
    """Single chat message data item."""
    type: str             # "user" | "assistant" | "tool"
    text: str             # Raw markdown (user/assistant) or plain text (tool)
    timestamp: str = ""
    tool_name: str = ""
    message_id: str = ""  # Persisted message id ("" for live, un-persisted messages)
    is_group_start: bool = True
    collapsed: bool = True  # Tool output collapsed by default


# ---------------------------------------------------------------------------
# Emoji manager (lazy-loads twemoji SVGs)
# ---------------------------------------------------------------------------

class _EmojiManager:
    """Loads and caches Twemoji SVG bitmaps."""

    def __init__(self):
        self._bitmaps: Dict[str, wx.Bitmap] = {}
        self._manifest: Dict[str, str] = {}
        self._base_path: str = ""
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True

        # Find assets directory
        base = os.path.dirname(os.path.abspath(__file__))  # .../berserker/gui/
        candidate = os.path.join(base, "..", "assets", "twemoji")
        self._base_path = os.path.abspath(candidate)

        # Load manifest
        manifest_path = os.path.join(self._base_path, "emoji_manifest.json")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    self._manifest = json.load(f)
            except Exception as e:
                logger.warning("Failed to load emoji manifest: %s", e)
        else:
            logger.warning("Emoji manifest not found at %s", manifest_path)

    def get_bitmap(self, name, size=_AVATAR_SIZE):
        # type: (str, int) -> Optional[wx.Bitmap]
        """Get an emoji bitmap by name (e.g. 'user-avatar', 'send').

        Loads PNG from assets/twemoji/72x72/, scales to requested size,
        and caches the result. Returns None on any failure.
        """
        self._ensure_loaded()
        cache_key = "{}@{}".format(name, size)

        if cache_key in self._bitmaps:
            return self._bitmaps[cache_key]

        png_name = self._manifest.get(name)
        if not png_name:
            return None

        png_path = os.path.join(self._base_path, "72x72", png_name)
        if not os.path.exists(png_path):
            return None

        try:
            img = wx.Image(png_path, wx.BITMAP_TYPE_PNG)
            if img.IsOk():
                if img.GetWidth() != size or img.GetHeight() != size:
                    img = img.Scale(size, size, wx.IMAGE_QUALITY_HIGH)
                bmp = wx.Bitmap(img)
                self._bitmaps[cache_key] = bmp
                return bmp
        except Exception as e:
            logger.debug("Failed to load emoji %s: %s", png_path, e)

        return None

    def get_initial_bmp(self, initial: str, size: int = _AVATAR_SIZE) -> wx.Bitmap:
        """Create a colored circle with initial letter as fallback avatar."""
        font = wx.Font(
            int(size * 0.5), wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD
        )
        # Measure initial
        dc = wx.MemoryDC()
        bmp = wx.Bitmap(size, size)
        dc.SelectObject(bmp)
        dc.SetBackground(wx.Brush(wx.Colour(0xE8, 0xE8, 0xED)))
        dc.Clear()

        # Draw circle
        gc = wx.GraphicsContext.Create(dc)
        if gc:
            gc.SetBrush(wx.Brush(wx.Colour(0x00, 0x7A, 0xFF)))
            gc.SetPen(wx.TRANSPARENT_PEN)
            gc.DrawEllipse(0, 0, size, size)

        # Draw initial
        dc.SetFont(font)
        dc.SetTextForeground(wx.Colour(0xFF, 0xFF, 0xFF))
        tw, th = dc.GetTextExtent(initial)
        dc.DrawText(
            initial,
            (size - tw) // 2,
            (size - th) // 2,
        )
        dc.SelectObject(wx.NullBitmap)
        return bmp


# Module-level singleton
_emoji_mgr = _EmojiManager()


# ---------------------------------------------------------------------------
# ChatListBox — the virtualized message list
# ---------------------------------------------------------------------------

class ChatListBox(wx.VListBox):
    """Virtualized chat message list with rounded bubble rendering.

    Each item in the list is one chat message. Items are virtualized —
    only visible items are rendered, making this efficient for large histories.

    Item 0 = oldest message, last item = newest message.
    """

    def __init__(self, parent):
        # type: (wx.Window) -> None
        super(ChatListBox, self).__init__(
            parent,
            style=wx.BORDER_NONE | wx.WANTS_CHARS,
        )

        # Data
        self._messages: List[ChatMessageData] = []  # type: List[ChatMessageData]
        self._group_starts: Set[int] = set()  # Indices that start a new visual group

        # Cached item heights (cleared on resize)
        self._line_heights: Dict[int, int] = {}  # type: Dict[int, int]
        self._cached_width: int = 0
        self._cached_line_h: int = 0  # Consistent line height for all measurements

        # Fonts (lazy initialized)
        self._fonts_initialized = False
        self._font_msg: Optional[wx.Font] = None
        self._font_time: Optional[wx.Font] = None
        self._font_tool: Optional[wx.Font] = None
        self._font_bold: Optional[wx.Font] = None

        # Search
        self._search_query: str = ""
        self._search_results: Set[int] = set()  # Item indices with matches

        # Message deletion callback (persists UI deletions to the session store)
        self._delete_cb = None  # type: Optional[Callable[[str], None]]

        # Background color
        self.SetBackgroundColour(_COLOR_BG_PANEL)

        # Bind resize event to invalidate cache
        self.Bind(wx.EVT_SIZE, self._on_size)

        # Bind mouse events
        self.Bind(wx.EVT_RIGHT_UP, self._on_right_up)
        self.Bind(wx.EVT_LEFT_DCLICK, self._on_left_dclick)
        self.Bind(wx.EVT_LEFT_UP, self._on_left_up)
        self.Bind(wx.EVT_MOUSEWHEEL, self._on_mouse_wheel)

    # -----------------------------------------------------------------------
    # Font initialization
    # -----------------------------------------------------------------------

    def _init_fonts(self):
        """Lazy-initialize fonts."""
        if self._fonts_initialized:
            return
        self._fonts_initialized = True

        self._font_msg = wx.Font(
            10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL,
            faceName="Segoe UI",
        )
        self._font_time = wx.Font(
            8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL,
            faceName="Segoe UI",
        )
        self._font_tool = wx.Font(
            9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL,
            faceName="Consolas",
        )
        self._font_bold = wx.Font(
            10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD,
            faceName="Segoe UI",
        )
        # Pre-compute consistent line height from MemoryDC
        bmp = wx.Bitmap(1, 1)
        mem = wx.MemoryDC()
        mem.SelectObject(bmp)
        mem.SetFont(self._font_msg)
        self._cached_line_h = mem.GetCharHeight()
        mem.SelectObject(wx.NullBitmap)

    def _lh(self):
        # type: () -> int
        """Get consistent line height (same value used by OnMeasureItem and OnDrawItem)."""
        if self._cached_line_h == 0:
            self._cached_line_h = 14  # fallback
        return self._cached_line_h

    # -----------------------------------------------------------------------
    # Message management
    # -----------------------------------------------------------------------

    def add_message(self, msg_type, text, timestamp=None, tool_name=""):
        # type: (str, str, Optional[str], str) -> None
        """Add a message and refresh display."""
        self._init_fonts()

        if timestamp is None:
            timestamp = datetime.now().strftime("%H:%M:%S")

        # Determine grouping
        is_group_start = True
        if self._messages:
            prev = self._messages[-1]
            if prev.type == msg_type:
                is_group_start = False

        msg = ChatMessageData(
            type=msg_type,
            text=text,
            timestamp=timestamp,
            tool_name=tool_name,
            is_group_start=is_group_start,
        )

        if is_group_start:
            self._group_starts.add(len(self._messages))

        self._messages.append(msg)
        self.SetItemCount(len(self._messages))

        # Invalidate cache for the new last item AND the previous last item
        self._line_heights.pop(len(self._messages) - 1, None)
        self._line_heights.pop(len(self._messages) - 2, None)

        # Refresh and scroll to bottom only if already at bottom
        self.Refresh()
        if self._is_at_bottom():
            self.scroll_to_bottom()

    def clear(self):
        # type: () -> None
        """Remove all messages."""
        self._messages.clear()
        self._group_starts.clear()
        self._line_heights.clear()
        self.SetItemCount(0)
        self.Refresh()

    def _rebuild_groups(self):
        # type: () -> None
        """Rebuild group-start bookkeeping from scratch (O(N), no refresh).

        Shared by set_messages / insert_front / _on_delete so group boundaries
        stay correct after any structural change.
        """
        self._group_starts.clear()
        for i, msg in enumerate(self._messages):
            if i == 0 or self._messages[i - 1].type != msg.type:
                self._group_starts.add(i)
                msg.is_group_start = True
            else:
                msg.is_group_start = False

    def set_messages(self, items):
        # type: (List[ChatMessageData]) -> None
        """Replace all messages in a single batch (one SetItemCount + Refresh).

        Used for initial history loads: avoids O(N) per-message layout churn.
        """
        self._init_fonts()
        self._messages = list(items)
        self._rebuild_groups()
        self._line_heights.clear()
        self.SetItemCount(len(self._messages))
        self.Refresh()

    def insert_front(self, items):
        # type: (List[ChatMessageData]) -> None
        """Insert older messages at the front, preserving the current viewport.

        Used by the 'Load earlier messages' pagination: only the newly
        revealed batch is inserted (no full rebuild), keeping the scroll
        position anchored to the same messages.
        """
        if not items:
            return
        self._init_fonts()
        old_first = self.GetVisibleBegin()
        self._messages = list(items) + self._messages
        self._rebuild_groups()
        self._line_heights.clear()
        self.SetItemCount(len(self._messages))
        self.Refresh()
        if old_first >= 0:
            self.ScrollToRow(old_first + len(items))

    def get_message_count(self):
        # type: () -> int
        """Get total number of messages."""
        return len(self._messages)

    def set_delete_callback(self, callback):
        # type: (Optional[Callable[[str], None]]) -> None
        """Set a callback(message_id) invoked when the user deletes a message.

        The callback is responsible for persisting the deletion; without it
        deletions only affect the in-memory list.
        """
        self._delete_cb = callback

    # -----------------------------------------------------------------------
    # Search support
    # -----------------------------------------------------------------------

    def search(self, query):
        # type: (str) -> int
        """Search messages and highlight matches. Returns match count."""
        self._search_query = query
        self._search_results.clear()
        if not query:
            self._search_results.clear()
            self.Refresh()
            return 0

        query_lower = query.lower()
        for i, msg in enumerate(self._messages):
            if query_lower in msg.text.lower():
                self._search_results.add(i)

        self.Refresh()
        return len(self._search_results)

    def clear_search(self):
        # type: () -> None
        """Clear search highlights."""
        self._search_query = ""
        self._search_results.clear()
        self.Refresh()

    # -----------------------------------------------------------------------
    # Scrolling
    # -----------------------------------------------------------------------

    def _is_at_bottom(self):
        # type: () -> bool
        """Check if the scrollbar is already at the bottom of the list."""
        try:
            end = self.GetVisibleEnd()
            return end >= len(self._messages)
        except Exception:
            return True  # Default to scroll on error

    def scroll_to_bottom(self):
        # type: () -> None
        """Scroll so the newest message's bubble bottom sits at the viewport bottom.

        Uses a geometrically computed target row instead of wx's approximate
        scrollbar thumb estimate, so the newest bubble lands flush even when
        row heights are mixed.
        """
        if not self._messages:
            return
        target = self._flush_first_row()
        self.ScrollToRow(target)
        # Re-assert once the layout settles (row heights may change)
        wx.CallAfter(self._adjust_bottom)

    def _flush_first_row(self):
        # type: () -> int
        """Return the row that should sit at the top so the newest bubble is
        FULLY visible (its bottom inside the viewport).

        Walks the rows from the end and keeps every row that still fits within
        the viewport (cumulative height <= viewport). This guarantees the last
        message is never clipped; leftover viewport space stays below it, which
        is the natural look for a chat list whose content is shorter than the
        window.
        """
        count = len(self._messages)
        if count == 0:
            return 0
        vp = self.GetClientSize().height
        if vp <= 0:
            return count - 1
        total = 0
        first_fit = count
        for i in range(count - 1, -1, -1):
            h = self._measure_item_height(i)
            if total + h > vp:
                break  # adding this row would push the last bubble out of view
            total += h
            first_fit = i
        if first_fit == count:
            # The last bubble alone is taller than the viewport; show it anyway.
            return count - 1
        return first_fit

    def _adjust_bottom(self):
        # type: () -> None
        """Re-assert the flush-bottom scroll position after layout settles.

        Reads the current geometry (not a stale visible-row count), so
        queued callbacks converge to the exact flush position without
        overshooting.
        """
        # Only adjust if user is still at the bottom
        if not self._is_at_bottom():
            return
        target = self._flush_first_row()
        try:
            if target != self.GetVisibleBegin():
                self.ScrollToRow(target)
        except Exception:
            pass

    def scroll_to_top(self):
        # type: () -> None
        """Scroll to the first (oldest) message."""
        self.ScrollToRow(0)

    # -----------------------------------------------------------------------
    # VListBox virtual methods
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Shared bubble geometry
    # -----------------------------------------------------------------------

    def _bubble_layout(self, cli_width, is_user, is_group_start):
        # type: (int, bool, bool) -> Tuple[int, int, int, int, int]
        """Compute bubble geometry shared by OnMeasureItem and OnDrawItem.

        Having a single source of truth for the layout prevents the
        measure-vs-draw divergences that used to cause clipped text and
        oversized bubble heights.

        Returns (avatar_left, bubble_left, bubble_right, bubble_width, avatar_size).
        """
        padding = _PADDING
        avatar_size = _AVATAR_SIZE if is_group_start else 0
        max_bubble_width = int(cli_width * 0.72)

        if is_user:
            # User: right-aligned
            avatar_left = cli_width - padding - avatar_size
            bubble_right = avatar_left - padding if avatar_size > 0 else cli_width - padding
            bubble_left = max(padding, cli_width - max_bubble_width - padding - avatar_size)
            if bubble_left < padding:
                bubble_left = padding
        else:
            # Assistant/Tool: left-aligned with avatar
            avatar_left = padding
            bubble_left = avatar_left + avatar_size + padding
            bubble_right = min(cli_width - padding, bubble_left + max_bubble_width)

        bubble_width = bubble_right - bubble_left
        if bubble_width < 100:
            bubble_left = padding if not is_user else (padding if avatar_size == 0 else cli_width - padding - max_bubble_width)
            bubble_width = cli_width - bubble_left - padding
            bubble_right = bubble_left + bubble_width

        return avatar_left, bubble_left, bubble_right, bubble_width, avatar_size

    def OnDrawItem(self, dc, rect, n):
        # type: (wx.DC, wx.Rect, int) -> None
        """Draw a single chat message bubble."""
        if n < 0 or n >= len(self._messages):
            return
        self._init_fonts()

        # Clip to item rect to prevent overflow into adjacent items
        dc.SetClippingRegion(rect.x, rect.y, rect.width, rect.height)

        msg = self._messages[n]
        is_group_start = n in self._group_starts
        cli_width = self.GetClientSize().width
        if cli_width <= 0:
            return

        # Use GraphicsContext for anti-aliased drawing
        gc = wx.GraphicsContext.Create(dc)
        if gc is None:
            return

        is_user = msg.type == "user"
        avatar_left, bubble_left, bubble_right, bubble_width, avatar_size = self._bubble_layout(
            cli_width, is_user, is_group_start
        )

        # Y position: 2px top margin
        y = rect.y + 2

        # Timestamp (only for group start)
        ts_used = 0
        if is_group_start and msg.timestamp:
            ts_used = 16  # 14 for text + 2 gap
            y += ts_used

        # Draw avatar (only for group start)
        if is_group_start:
            self._draw_avatar(gc, dc, avatar_left, rect.y + 4, avatar_size, msg)

        # Bubble height: fill item minus margins
        bubble_top_margin = 2
        bubble_bottom_margin = 4
        bubble_height = rect.height - bubble_top_margin - bubble_bottom_margin - ts_used

        # Draw bubble background
        if msg.type == "user":
            bg = _COLOR_USER_BUBBLE
            border = wx.Colour(0xC3, 0xDD, 0xF1)  # soft blue border matched to bubble
        elif msg.type == "assistant":
            bg = _COLOR_ASST_BUBBLE
            border = _COLOR_BUBBLE_BORDER
        else:
            bg = _COLOR_TOOL_BUBBLE
            border = wx.Colour(0xDD, 0xDD, 0xE4)  # cool gray border matched to bubble

        self._draw_bubble(gc, bubble_left, y, bubble_width, bubble_height, bg, border)

        # Draw timestamp (only for group start)
        if is_group_start and msg.timestamp:
            dc.SetFont(self._font_time)
            dc.SetTextForeground(_COLOR_TIMESTAMP)
            if is_user:
                ts_x = bubble_right - 4 - dc.GetTextExtent(msg.timestamp)[0]
            else:
                ts_x = bubble_left + 4
            dc.DrawText(msg.timestamp, ts_x, rect.y + 3)

        # Draw message text inside bubble
        text_inset = 6  # vertical space from bubble edge to text (top/bottom)
        text_side = 10  # horizontal space from bubble edge to text (left/right)
        text_x = bubble_left + text_side
        text_y = y + text_inset
        text_w = max(60, bubble_width - text_side * 2)  # matches OnMeasureItem
        # Text height fills bubble minus insets
        text_h = bubble_height - text_inset * 2

        text_rect = wx.Rect(text_x, text_y, text_w, text_h)
        is_match = n in self._search_results

        self._draw_message_text(dc, gc, msg, text_rect, is_match)

        gc.Destroy() if hasattr(gc, 'Destroy') else None
        dc.DestroyClippingRegion()

    def OnMeasureItem(self, n):
        # type: (int) -> int
        """Calculate item height based on message content using plain-text word wrap."""
        if n < 0 or n >= len(self._messages):
            return 40

        cli_width = self.GetClientSize().width
        if n in self._line_heights and self._cached_width == cli_width:
            return self._line_heights[n]

        height = self._measure_item_height(n)

        self._line_heights[n] = height
        self._cached_width = cli_width
        return height

    def _measure_item_height(self, n):
        # type: (int) -> int
        """Compute the natural height of item n.

        Shared by OnMeasureItem and the flush-row geometry so measurement and
        drawing always agree.
        """
        self._init_fonts()
        msg = self._messages[n]
        is_group_start = n in self._group_starts

        # Available text width (must match OnDrawItem bubble layout)
        is_user = msg.type == "user"
        cli_width = self.GetClientSize().width
        _, _, _, bubble_width, _ = self._bubble_layout(cli_width, is_user, is_group_start)
        text_side = 10
        text_width = max(60, int(bubble_width - text_side * 2))

        # Create MemoryDC for text measurement
        bmp = wx.Bitmap(1, 1)
        mem = wx.MemoryDC()
        mem.SelectObject(bmp)
        mem.SetFont(self._font_msg)

        line_h = self._lh()

        if msg.type == "tool":
            mem.SetFont(self._font_tool)
            display_lines, show_toggle, _ = self._tool_layout(msg, mem, text_width)
            n_lines = len(display_lines) + (1 if show_toggle else 0)
        else:
            mem.SetFont(self._font_msg)
            wrapped = self._wrap_text(msg.text, mem, text_width)
            n_lines = len(wrapped)

        text_total_h = max(line_h, n_lines * line_h)

        # Vertical layout (must match OnDrawItem)
        text_inset = 6
        top_margin = 2
        bottom_margin = 4
        ts_used = 16 if (is_group_start and msg.timestamp) else 0

        height = (top_margin + ts_used + text_inset +
                  text_total_h +
                  text_inset + bottom_margin)

        if height < 50:
            height = 50

        mem.SelectObject(wx.NullBitmap)
        return height

    # -----------------------------------------------------------------------
    # Drawing helpers
    # -----------------------------------------------------------------------

    def _draw_bubble(self, gc, x, y, w, h, bg_color, border_color):
        # type: (wx.GraphicsContext, float, float, float, float, wx.Colour, wx.Colour) -> None
        """Draw a rounded rectangle bubble."""
        if w <= 0 or h <= 0:
            return
        radius = _BUBBLE_RADIUS
        path = gc.CreatePath()
        path.AddRoundedRectangle(x, y, w, h, radius)
        path.CloseSubpath()

        # Fill
        gc.SetBrush(wx.Brush(bg_color))
        gc.SetPen(wx.TRANSPARENT_PEN)
        gc.FillPath(path)

        # Border
        border_path = gc.CreatePath()
        border_path.AddRoundedRectangle(x, y, w, h, radius)
        border_path.CloseSubpath()
        gc.SetPen(wx.Pen(border_color, 1))
        gc.StrokePath(border_path)

    def _draw_avatar(self, gc, dc, x, y, size, msg):
        # type: (wx.GraphicsContext, wx.DC, float, float, float, ChatMessageData) -> None
        """Draw an avatar circle (emoji or initial)."""
        if msg.type == "user":
            bmp = _emoji_mgr.get_bitmap("user-avatar", size)
        elif msg.type == "tool":
            bmp = _emoji_mgr.get_bitmap("tool", size)
        else:
            bmp = _emoji_mgr.get_bitmap("bot-avatar", size)
            if bmp is None:
                # Try info emoji as fallback
                bmp = _emoji_mgr.get_bitmap("info", size)

        if bmp:
            gc.DrawBitmap(bmp, x, y, size, size)
        else:
            # Fallback: colored circle with initial
            initial = "U" if msg.type == "user" else ("T" if msg.type == "tool" else "A")
            fbmp = _emoji_mgr.get_initial_bmp(initial, size)
            gc.DrawBitmap(fbmp, x, y, size, size)

    # -----------------------------------------------------------------------
    # Plain-text word wrapping
    # -----------------------------------------------------------------------

    def _wrap_text(self, text, dc, max_width):
        # type: (str, wx.DC, int) -> List[str]
        """Split text into lines that fit within max_width pixels.

        Splits on spaces first, then breaks long words character-by-character.
        Preserves empty lines (blank paragraphs).
        Uses whatever font is currently set on the DC.
        """
        if not text:
            return [""]
        wrapped = []
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                wrapped.append("")
                continue
            words = paragraph.split(" ")
            current_line = ""
            for word in words:
                if not word:
                    # Preserve space between words as single space
                    if current_line:
                        current_line += " "
                    continue
                # Add word if fits
                candidate = current_line + word if not current_line.endswith(" ") else current_line + " " + word
                # Actually just test: current_line + " " + word, but handle empty current_line
                if current_line:
                    test = current_line + " " + word
                else:
                    test = word
                tw, _ = dc.GetTextExtent(test)
                if tw <= max_width:
                    current_line = test
                else:
                    if current_line:
                        wrapped.append(current_line)
                        current_line = ""
                    # Handle long single word: character-break
                    ww, _ = dc.GetTextExtent(word)
                    if ww > max_width:
                        part = ""
                        for ch in word:
                            test_p = part + ch
                            tp, _ = dc.GetTextExtent(test_p)
                            if tp > max_width:
                                wrapped.append(part)
                                part = ch
                            else:
                                part = test_p
                        current_line = part
                    else:
                        current_line = word
            if current_line:
                wrapped.append(current_line)
        return wrapped

    def _tool_layout(self, msg, dc, width):
        # type: (ChatMessageData, wx.DC, int) -> Tuple[List[str], bool, int]
        """Compute the draw layout for a tool message.

        Collapsed: wraps a bounded preview (first 2000 chars) and caps the
        displayed lines at 5, so huge tool outputs stay cheap to measure and
        render.
        Expanded: wraps the full text so the complete output is visible.

        The font used for wrapping must already be set on ``dc``.

        Returns (display_lines, show_toggle, full_line_count).
        """
        if not msg.collapsed:
            lines = self._wrap_text(msg.text, dc, width)
            return lines, True, len(lines)

        preview = msg.text[:2000]
        lines = self._wrap_text(preview, dc, width)
        has_more = len(msg.text) > 2000 or len(lines) > 5
        if has_more:
            return lines[:5], True, len(lines)
        return lines, False, len(lines)

    def _draw_message_text(self, dc, gc, msg, rect, is_match):
        # type: (wx.DC, wx.GraphicsContext, ChatMessageData, wx.Rect, bool) -> None
        """Draw message text (plain text with word wrap)."""
        # Choose colors
        if msg.type == "user":
            text_color = _COLOR_USER_TEXT
        elif msg.type == "assistant":
            text_color = _COLOR_ASST_TEXT
        else:
            text_color = _COLOR_TOOL_TEXT

        # Tool messages: plain text with collapse, word-wrapped with _font_tool
        if msg.type == "tool":
            y = rect.y
            dc.SetFont(self._font_tool)
            display_lines, show_toggle, full_count = self._tool_layout(msg, dc, rect.width)

            dc.SetTextForeground(text_color)
            for line in display_lines:
                if is_match and self._search_query and self._search_query.lower() in line.lower():
                    tw, th = dc.GetTextExtent(line)
                    dc.SetBrush(wx.Brush(_COLOR_SEARCH_HIGHLIGHT))
                    dc.SetPen(wx.TRANSPARENT_PEN)
                    dc.DrawRectangle(int(rect.x), int(y), min(tw, rect.width), th)
                dc.DrawText(line, rect.x, y)
                y += self._lh()

            # Show "Show more" / "Show less" indicator
            if show_toggle:
                dc.SetFont(self._font_msg)
                dc.SetTextForeground(_COLOR_LINK)
                if msg.collapsed:
                    label = "▼ Show more"
                else:
                    label = "▲ Show less ({} lines total)".format(full_count)
                dc.DrawText(label, rect.x, y)
            return

        # User / Assistant messages: plain text with word wrap
        dc.SetFont(self._font_msg)
        lines = self._wrap_text(msg.text, dc, rect.width)
        y = rect.y
        dc.SetTextForeground(text_color)
        for line in lines:
            if is_match and self._search_query and self._search_query.lower() in line.lower():
                tw, th = dc.GetTextExtent(line)
                dc.SetBrush(wx.Brush(_COLOR_SEARCH_HIGHLIGHT))
                dc.SetPen(wx.TRANSPARENT_PEN)
                dc.DrawRectangle(int(rect.x), int(y), min(tw, rect.width), th)
            dc.SetFont(self._font_msg)
            dc.DrawText(line, int(rect.x), int(y))
            y += self._lh()
        return

    # -----------------------------------------------------------------------
    # Event handlers
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Event handlers
    # -----------------------------------------------------------------------

    def _on_size(self, event):
        # type: (wx.SizeEvent) -> None
        """Handle resize — invalidate height cache and recalc."""
        self._line_heights.clear()
        self._cached_width = 0
        self.Refresh()
        event.Skip()

    # -----------------------------------------------------------------------
    # Context menu
    # -----------------------------------------------------------------------

    def OnDrawBackground(self, dc, rect, n):
        # type: (wx.DC, wx.Rect, int) -> None
        """Draw background for an item."""
        if n == -1:
            # This is the area beyond the last item
            dc.SetBrush(wx.Brush(_COLOR_BG_PANEL))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.DrawRectangle(rect)
        # For regular items, we draw our own background in OnDrawItem

    # -----------------------------------------------------------------------
    # Keyboard / Mouse
    # -----------------------------------------------------------------------

    def _on_right_up(self, event):
        # type: (wx.MouseEvent) -> None
        """Handle right-click: show context menu."""
        self._show_context_menu(event)
        event.Skip()

    def _on_left_dclick(self, event):
        # type: (wx.MouseEvent) -> None
        """Handle double-click: show selectable text dialog."""
        row = self.VirtualHitTest(event.GetPosition().y)
        if 0 <= row < len(self._messages):
            self._show_text_dialog(row)
        event.Skip()

    def _on_left_up(self, event):
        # type: (wx.MouseEvent) -> None
        """Handle left click — toggle collapse on tool message."""
        row = self.VirtualHitTest(event.GetPosition().y)
        if 0 <= row < len(self._messages):
            msg = self._messages[row]
            if msg.type == "tool":
                self._toggle_collapse(row)
                return
        event.Skip()

    def _toggle_collapse(self, row):
        # type: (int) -> None
        """Toggle collapsed state of a tool message and refresh."""
        if 0 <= row < len(self._messages):
            msg = self._messages[row]
            if msg.type == "tool":
                msg.collapsed = not msg.collapsed
                # Recalculate height
                self._line_heights.pop(row, None)
                self.Refresh()

    def _on_mouse_wheel(self, event):
        # type: (wx.MouseEvent) -> None
        """Scroll by rows per wheel tick.

        Handles arbitrary wheel rotations (not only multiples of 120) so
        high-resolution wheels still scroll. Scrolling down into the end snaps
        to the geometrically flush bottom, because wx's row-based scrollbar may
        stop short of it and leave the newest bubble floating above the bottom.
        """
        rotation = event.GetWheelRotation()
        if rotation == 0:
            event.Skip()
            return
        lines_per = max(1, event.GetLinesPerAction())
        steps = max(1, abs(rotation) // 120)
        # Wheel up (rotation > 0) scrolls up (negative rows)
        direction = -1 if rotation > 0 else 1
        self.ScrollRows(direction * steps * lines_per)
        if direction > 0:
            self._snap_to_bottom_if_at_end()

    def _snap_to_bottom_if_at_end(self):
        # type: () -> None
        """Snap the view to the flush-bottom position when scrolled to the end."""
        if not self._is_at_bottom():
            return
        target = self._flush_first_row()
        try:
            begin = self.GetVisibleBegin()
        except Exception:
            return
        if begin != target:
            self.ScrollToRow(target)

    def _show_text_dialog(self, row):
        # type: (int) -> None
        """Show a dialog with HTML-rendered markdown."""
        msg = self._messages[row]
        label = "User" if msg.type == "user" else ("Tool: " + msg.tool_name if msg.tool_name else "Assistant")

        # Convert markdown to HTML
        if msg.type in ("user", "assistant"):
            try:
                import mistune
                from mistune.plugins.table import table as plugin_table
                md = mistune.create_markdown(plugins=[plugin_table])
                html_body = md(msg.text)
            except Exception:
                html_body = "<pre>" + _html_escape(msg.text) + "</pre>"
        else:
            html_body = "<pre>" + _html_escape(msg.text) + "</pre>"

        css = (
            "body{font-family:'Segoe UI',sans-serif;font-size:14px;line-height:1.6;"
            "padding:16px;color:#1D1D1F;background:#fff;margin:0;}"
            "pre,code{font-family:'Consolas','Courier New',monospace;background:#f5f5f5;"
            "border-radius:4px;padding:2px 6px;font-size:13px;}"
            "pre{padding:12px;overflow-x:auto;border:1px solid #e0e0e0;}"
            "pre code{background:none;padding:0;border-radius:0;}"
            "table{border-collapse:collapse;width:100%;margin:8px 0;}"
            "th,td{border:1px solid #d0d0d0;padding:6px 10px;text-align:left;}"
            "th{background:#f0f0f0;font-weight:600;}"
            "h1,h2,h3,h4{margin:16px 0 8px;color:#111;}"
            "p{margin:0 0 8px;}"
            "blockquote{border-left:4px solid #007AFF;margin:8px 0;padding:4px 12px;color:#555;background:#f8f9fa;}"
            "ul,ol{margin:4px 0;padding-left:24px;}"
            "a{color:#007AFF;}"
            "img{max-width:100%;}"
        )

        full_html = "<!doctype html><html><head><meta charset='utf-8'><style>{}</style></head><body>{}</body></html>".format(
            css, html_body
        )

        dlg = wx.Dialog(self, title="{} - Markdown".format(label),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX | wx.MINIMIZE_BOX)
        dlg.SetSize(700, 500)
        dlg.SetMinSize(wx.Size(400, 300))
        sizer = wx.BoxSizer(wx.VERTICAL)

        has_webview = False
        try:
            web = wx.html2.WebView.New(dlg)
            web.SetPage(full_html, "")
            has_webview = True
        except Exception:
            pass

        if not has_webview:
            web = wx.html.HtmlWindow(dlg)
            web.SetPage(full_html)

        sizer.Add(web, 1, wx.EXPAND | wx.ALL, 10)

        # Bottom bar: raw markdown copy button + close
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        copy_raw_btn = wx.Button(dlg, wx.ID_ANY, "复制原始 Markdown")
        copy_raw_btn.Bind(wx.EVT_BUTTON, lambda e: self._copy_to_clipboard(msg.text))
        btn_sizer.Add(copy_raw_btn, 0)
        btn_sizer.AddStretchSpacer()
        close_btn = wx.Button(dlg, wx.ID_CLOSE, "关闭")
        close_btn.Bind(wx.EVT_BUTTON, lambda e: dlg.Close())
        btn_sizer.Add(close_btn, 0)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        dlg.SetSizer(sizer)
        dlg.ShowModal()
        dlg.Destroy()

    def _show_context_menu(self, event):
        # type: (wx.MouseEvent) -> None
        """Show right-click context menu."""
        row = self.VirtualHitTest(event.GetPosition().y)
        if row < 0 or row >= len(self._messages):
            return

        msg = self._messages[row]
        menu = wx.Menu()

        copy_item = menu.Append(wx.ID_COPY, "复制\tCtrl+C")
        self.Bind(wx.EVT_MENU, lambda e, r=row: self._on_copy(r), copy_item)

        select_all = menu.Append(wx.ID_SELECTALL, "全选\tCtrl+A")
        self.Bind(wx.EVT_MENU, lambda e: self._on_select_all(), select_all)

        # Collapse/expand toggle for tool messages
        if msg.type == "tool":
            menu.AppendSeparator()
            toggle_label = "收起输出" if not msg.collapsed else "展开全部"
            toggle_item = menu.Append(wx.ID_ANY, toggle_label)
            self.Bind(wx.EVT_MENU, lambda e, r=row: self._toggle_collapse(r), toggle_item)

        if not msg.type == "user":
            menu.AppendSeparator()
            delete_item = menu.Append(wx.ID_DELETE, "删除")
            self.Bind(wx.EVT_MENU, lambda e, r=row: self._on_delete(r), delete_item)

        self.PopupMenu(menu)
        menu.Destroy()

    def _on_copy(self, row):
        # type: (int) -> None
        """Copy message text to clipboard."""
        if 0 <= row < len(self._messages):
            self._copy_to_clipboard(self._messages[row].text)

    def _on_select_all(self):
        # type: () -> None
        """Select all text (copy to clipboard since we can't select in VListBox)."""
        full_text = "\n\n".join(m.text for m in self._messages)
        self._copy_to_clipboard(full_text)

    def _on_delete(self, row):
        # type: (int) -> None
        """Delete a message (persists via callback when a message id exists)."""
        if 0 <= row < len(self._messages):
            msg = self._messages[row]
            if msg.message_id and self._delete_cb:
                try:
                    self._delete_cb(msg.message_id)
                except Exception:
                    logger.exception("Delete callback failed for message %s", msg.message_id)
            self._messages.pop(row)
            self._rebuild_groups()
            self._line_heights.clear()
            self.SetItemCount(len(self._messages))
            self.Refresh()

    @staticmethod
    def _copy_to_clipboard(text):
        # type: (str) -> None
        """Copy text to clipboard."""
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(text))
            wx.TheClipboard.Close()
