"""
berserker.gui.md_to_plain — Convert Markdown to plain text with tabulate-aligned tables.

Uses Mistune AST for accurate parsing (no false positives inside code blocks),
then renders tables via tabulate and everything else as plain text.

Python 3.8.10 compatible.
"""

from __future__ import annotations

from typing import Any, Dict, List

import mistune
from mistune.plugins.table import table as plugin_table
from tabulate import tabulate

# Mistune AST parser (singleton)
_md = None  # type: ignore


def _get_parser():
    global _md
    if _md is None:
        _md = mistune.create_markdown(renderer="ast", plugins=[plugin_table])
    return _md


def _collect_text(children):
    # type: (List[Dict[str, Any]]) -> str
    """Extract plain text from inline AST children."""
    parts = []
    for child in children or []:
        t = child.get("type", "")
        if t == "text":
            parts.append(child.get("raw", ""))
        elif t == "strong":
            parts.append(_collect_text(child.get("children", [])))
        elif t == "emphasis":
            parts.append(_collect_text(child.get("children", [])))
        elif t == "codespan":
            parts.append("`" + child.get("raw", "") + "`")
        elif t == "link":
            parts.append(_collect_text(child.get("children", [])) + " (" + child.get("raw", "") + ")")
        elif t == "image":
            parts.append(child.get("alt", ""))
        elif t == "softbreak":
            parts.append(" ")
        elif t == "linebreak":
            parts.append("\n")
        else:
            parts.append(child.get("raw", "") or "")
    return "".join(parts)


def _parse_table(node):
    # type: (Dict[str, Any]) -> tuple
    """Parse an AST table node into (headers, rows)."""
    headers = []
    rows = []
    for child in node.get("children", []):
        ct = child.get("type", "")
        if ct == "table_head":
            for cell in child.get("children", []):
                headers.append(_collect_text(cell.get("children", [])))
        elif ct == "table_body":
            for row in child.get("children", []):
                cells = []
                for cell in row.get("children", []):
                    cells.append(_collect_text(cell.get("children", [])))
                rows.append(cells)
    return headers, rows


def _parse_list(node):
    # type: (Dict[str, Any]) -> List[str]
    """Parse AST list node into list item strings."""
    items = []
    for child in node.get("children", []):
        if child.get("type") == "list_item":
            text_parts = []
            for c in child.get("children", []):
                if c.get("type") == "block_text":
                    text_parts.append(_collect_text(c.get("children", [])))
                else:
                    text_parts.append(_collect_text([c]))
            items.append("".join(text_parts))
    return items


def md_to_plain(text):
    # type: (str) -> str
    """Convert markdown to plain text with ASCII tables.

    Tables are rendered using tabulate's 'grid' format.
    Code blocks, headings, lists, etc. are converted to plain text.
    """
    if not text or not text.strip():
        return text

    try:
        parser = _get_parser()
        ast = parser(text)
    except Exception:
        # Fallback: return original text on parse failure
        return text

    lines = []  # type: List[str]

    for node in ast:
        t = node.get("type", "")

        if t == "heading":
            level = node.get("attrs", {}).get("level", 1) if isinstance(node.get("attrs"), dict) else 1
            prefix = "#" * level + " "
            text = _collect_text(node.get("children", []))
            lines.append(prefix + text)

        elif t == "paragraph":
            lines.append(_collect_text(node.get("children", [])))

        elif t == "table":
            headers, rows = _parse_table(node)
            if rows:
                ncols = max(len(r) for r in rows) if rows else 0
                if headers and all(h for h in headers):
                    ncols = max(ncols, len(headers))
                mw = [40] * ncols if ncols else None
                kwargs = {}
                if mw:
                    kwargs["maxcolwidths"] = mw
                if headers and all(h for h in headers):
                    lines.append(tabulate(rows, headers=headers, tablefmt="simple", **kwargs))
                else:
                    lines.append(tabulate(rows, tablefmt="simple", **kwargs))
            elif headers:
                lines.append(" | ".join(headers))

        elif t == "block_code":
            code = node.get("raw", "")
            lang = ""
            attrs = node.get("attrs")
            if attrs and isinstance(attrs, dict):
                lang = attrs.get("info", "")
            if lang:
                lines.append("[code: {}]".format(lang))
            lines.append(code.rstrip("\n"))

        elif t == "thematic_break":
            lines.append("---")

        elif t == "list":
            items = _parse_list(node)
            ordered = False
            attrs = node.get("attrs")
            if attrs and isinstance(attrs, dict):
                ordered = attrs.get("ordered", False)
            for idx, item in enumerate(items):
                marker = "{}. ".format(idx + 1) if ordered else "- "
                lines.append(marker + item)

        elif t == "block_quote":
            quote_text = _collect_text(node.get("children", []))
            for qline in quote_text.split("\n"):
                lines.append("> " + qline)

        elif t == "blank_line":
            if lines and lines[-1] != "":
                lines.append("")

    # Clean up: remove trailing blank lines
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)
