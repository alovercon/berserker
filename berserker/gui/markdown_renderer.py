"""berserker.gui.markdown_renderer — Markdown to wx.html-compatible HTML.

Renders Markdown into the legacy HTML flavour understood by ``wx.html.HtmlWindow``
(old-style attributes such as ``<font size>``, ``<tt>``, ``<table border>`` and
``<hr size>`` instead of CSS), so chat bubbles and dialogs can display rich
content even without a WebView backend.

Uses Mistune for parsing (with the table plugin). Python 3.8.10 compatible.
"""

from __future__ import annotations

import re

import mistune
from mistune.plugins.table import table as plugin_table

# Mistune renderer (singleton, table plugin enabled)
_md = mistune.create_markdown(plugins=[plugin_table])

# Heading level -> <font size> value (wx.html supports 1..7)
_HEADING_SIZES = {
    1: 7,
    2: 6,
    3: 5,
    4: 4,
    5: 3,
    6: 2,
}

_HEADING_RE = {
    level: re.compile(r"<h%d>(.*?)</h%d>" % (level, level), re.S)
    for level in _HEADING_SIZES
}


def _convert_headings(html):
    # type: (str) -> str
    """Convert <h1..h6> to <font size="N"><b>...</b></font> (wx.html style)."""
    for level, size in _HEADING_SIZES.items():
        def repl(m, s=size):
            # type: (re.Match, int) -> str
            return '<font size="%d"><b>%s</b></font>' % (s, m.group(1))
        html = _HEADING_RE[level].sub(repl, html)
    return html


def _convert_code(html):
    # type: (str) -> str
    """Convert <code> to <tt> and strip language classes from code blocks."""
    # fenced blocks: <pre><code class="lang">...</code></pre>
    html = html.replace("<pre><code", "<pre><tt")
    html = html.replace("</code></pre>", "</tt></pre>")
    # inline code
    html = re.sub(r"<code>(.*?)</code>", r"<tt>\1</tt>", html, flags=re.S)
    # strip remaining class attributes from <tt>
    html = re.sub(r"<tt class=\"[^\"]*\">", "<tt>", html)
    return html


def markdown_to_html(md, text_color=None, bg_color=None, font_size=None):
    # type: (Optional[str], Optional[str], Optional[str], Optional[int]) -> str
    """Convert Markdown to wx.html.HtmlWindow-compatible HTML.

    Args:
        md: Markdown source text.
        text_color: Optional body text color (e.g. "#FF0000").
        bg_color: Optional body background color (e.g. "#FFFFFF").
        font_size: Optional base font size (1-7); applied via a wrapping
            <font size="..."> tag.

    Returns:
        A complete HTML document string.
    """
    if not md or not md.strip():
        return "<html><body></body></html>"

    html = _md(md)

    # Emphasis: <strong>/<em> -> <b>/<i> (wx.html friendly)
    html = html.replace("<strong>", "<b>").replace("</strong>", "</b>")
    html = html.replace("<em>", "<i>").replace("</em>", "</i>")

    # Headings
    html = _convert_headings(html)

    # Code (inline + fenced blocks)
    html = _convert_code(html)

    # Tables: add the legacy border attribute wx.html understands
    html = re.sub(
        r"<table(?![^>]*border=)[^>]*>",
        '<table border="1" cellpadding="4" cellspacing="0">',
        html,
    )

    # Horizontal rules
    html = re.sub(r"<hr\s*/?>", '<hr size="1">', html)

    # Body wrapper with optional colours / base font size
    attrs = []
    if text_color:
        attrs.append('text="%s"' % text_color)
    if bg_color:
        attrs.append('bgcolor="%s"' % bg_color)
    body_attrs = (" " + " ".join(attrs)) if attrs else ""
    body = html
    if font_size is not None:
        size = max(1, min(7, int(font_size)))
        body = '<font size="%d">%s</font>' % (size, html)

    return "<html><body%s>%s</body></html>" % (body_attrs, body)
