"""berserker - The open source AI coding agent (Python port).

Compatibility layer: delegates to cli_entry.main()
so `python -m berserker` still works.
"""

from __future__ import annotations

from berserker.cli_entry import main

main()
