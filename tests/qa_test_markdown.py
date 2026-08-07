"""Manual QA test script for Markdown rendering."""

from berserker.gui.markdown_renderer import markdown_to_html

md = """# Test Heading

This is **bold** and *italic* text.

## Table Example

| Name | Age |
|------|-----|
| Alice | 30 |
| Bob | 25 |

### Code Block

```python
def hello():
    print('Hello!')
```

- Item 1
- Item 2
"""

html = markdown_to_html(md)
print("Generated HTML length:", len(html))
print("Contains heading:", "Test Heading" in html)
print("Contains bold:", "<b>" in html)
print("Contains table:", "<table" in html)
print("Contains code:", "<pre" in html)
print("Contains list:", "<ul" in html)
print("\n--- HTML Preview (first 500 chars) ---")
print(html[:500])
