"""
berserker.display — Display abstraction layer for CLI/GUI decoupling.
"""

from berserker.display.adapter import DisplayAdapter, CLIDisplayAdapter

__all__ = ["DisplayAdapter", "CLIDisplayAdapter"]
