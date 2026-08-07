"""
tests/test_markdown_renderer.py — Unit tests for markdown_renderer module.

Covers: headings, bold/italic, tables, code blocks, lists, inline code.
Python 3.8.10 compatible.
"""

from __future__ import annotations

import pytest

from berserker.gui.markdown_renderer import markdown_to_html


class TestMarkdownToHtml:
    """Tests for markdown_to_html() conversion."""

    def test_headings(self):
        """Headings are converted to <b><font> tags with appropriate sizes."""
        md = "# Heading 1\n## Heading 2\n### Heading 3"
        html = markdown_to_html(md)

        assert "<b>" in html
        assert "<font size=" in html
        # Should contain heading text
        assert "Heading 1" in html
        assert "Heading 2" in html
        assert "Heading 3" in html

    def test_bold_and_italic(self):
        """Bold and italic are converted to <b> and <i> tags."""
        md = "**bold text** and *italic text*"
        html = markdown_to_html(md)

        assert "<b>bold text</b>" in html
        assert "<i>italic text</i>" in html

    def test_code_block(self):
        """Fenced code blocks are converted to styled <pre> tags."""
        md = "```python\nprint('hello')\n```"
        html = markdown_to_html(md)

        assert "<pre" in html
        # codehilite extension may HTML-escape quotes, so check for either form
        assert "print" in html and "hello" in html
        assert "font-family" in html or "Consolas" in html or "tt" in html

    def test_inline_code(self):
        """Inline code is converted to styled <tt> tags."""
        md = "Use the `print()` function"
        html = markdown_to_html(md)

        assert "<tt" in html
        assert "print()" in html

    def test_table(self):
        """Tables are converted with borders and styled headers."""
        md = "| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |"
        html = markdown_to_html(md)

        assert "<table" in html
        assert "border=" in html
        assert "<th" in html
        assert "Alice" in html
        assert "Bob" in html

    def test_unordered_list(self):
        """Unordered lists are converted with proper styling."""
        md = "- Item 1\n- Item 2\n- Item 3"
        html = markdown_to_html(md)

        assert "<ul" in html
        assert "<li>" in html
        assert "Item 1" in html
        assert "Item 2" in html
        assert "Item 3" in html

    def test_ordered_list(self):
        """Ordered lists are converted with proper styling."""
        md = "1. First\n2. Second\n3. Third"
        html = markdown_to_html(md)

        assert "<ol" in html
        assert "<li>" in html
        assert "First" in html
        assert "Second" in html
        assert "Third" in html

    def test_mixed_content(self):
        """Mixed Markdown content renders correctly."""
        md = """# Title

This is a **bold** statement and an *italic* one.

## Code Example

```python
def hello():
    print("Hello, world!")
```

| Feature | Status |
|---------|--------|
| Markdown | Done |
| Tables | Done |

- List item 1
- List item 2
"""
        html = markdown_to_html(md)

        assert "<b>" in html
        assert "<i>" in html
        assert "<pre" in html
        assert "<table" in html
        assert "<ul" in html
        assert "Hello, world!" in html
        assert "Markdown" in html

    def test_empty_input(self):
        """Empty input returns valid HTML structure."""
        html = markdown_to_html("")

        assert "<html>" in html
        assert "</html>" in html

    def test_custom_colors(self):
        """Custom text and background colors are applied."""
        md = "**test**"
        html = markdown_to_html(md, text_color="#FF0000", bg_color="#000000")

        assert "#FF0000" in html
        assert "#000000" in html

    def test_custom_font_size(self):
        """Custom font size is applied."""
        md = "# Large Heading"
        html = markdown_to_html(md, font_size=14)

        assert "<font size=" in html
        assert "Large Heading" in html

    def test_horizontal_rule(self):
        """Horizontal rules are converted to <hr> with HTML attributes."""
        md = "Before\n\n---\n\nAfter"
        html = markdown_to_html(md)

        assert "<hr" in html
        # wx.html uses HTML attributes, not CSS
        assert "size=" in html
