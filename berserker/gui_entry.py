"""berserker-gui - GUI entry point for berserker.

Standalone GUI launcher. Supports command-line arguments:
    --project <dir>   Set workspace directory
    --config <path>   Set config file path
    --session <id>    Start with specific session

Usage:
    python -m berserker.gui_entry
    python -m berserker.gui_entry --project E:\myproject
    python berserker/gui_entry.py --config berserker.json
"""

from __future__ import annotations

import argparse
import os
import sys


def _verify_qwen_vocab():
    # type: () -> None
    """Verify qwen.tiktoken vocab file is available at startup."""
    try:
        from berserker.session.qwen_tokenizer import _find_vocab_path

        vocab_path = _find_vocab_path()
        if vocab_path:
            print("[INFO] qwen.tiktoken found: {}".format(vocab_path))
        else:
            print("[WARNING] qwen.tiktoken not found. Using embedded minimal vocab.")
    except Exception:
        pass


def main():
    # type: () -> int
    """Start the berserker GUI application."""
    # Verify qwen.tiktoken availability
    _verify_qwen_vocab()

    # Check wxPython availability
    try:
        import wx  # noqa: F401
    except ImportError:
        print("Error: wxPython is not installed.", file=sys.stderr)
        print("Install it with: pip install wxPython", file=sys.stderr)
        return 1

    # Parse arguments
    parser = argparse.ArgumentParser(description="berserker GUI")
    parser.add_argument("--project", type=str, help="Workspace directory")
    parser.add_argument("--config", type=str, help="Config file path")
    parser.add_argument("--session", type=str, help="Session ID to start with")
    args = parser.parse_args()

    # Determine workspace with priority:
    #   1. --project CLI argument (explicit override)
    #   2. Last workspace from GUI state (remembered from previous session)
    #   3. First-launch directory picker (wx.DirDialog)
    if args.project:
        workspace = os.path.abspath(args.project)
    else:
        from berserker.gui.state import get_last_workspace, set_last_workspace

        workspace = get_last_workspace()
        if workspace is None:
            # First launch: show directory picker
            import wx

            app = wx.App(False)
            app.SetAppName("berserker")
            dlg = wx.DirDialog(
                None,
                message="Choose your workspace directory",
                defaultPath=os.path.expanduser("~"),
                style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST,
            )
            if dlg.ShowModal() == wx.ID_OK:
                workspace = dlg.GetPath()
            else:
                # User cancelled — exit gracefully
                dlg.Destroy()
                app.Destroy()
                print("[INFO] No workspace selected. Exiting.")
                return 0
            dlg.Destroy()
            app.Destroy()

        workspace = os.path.abspath(workspace)
        # Remember the workspace for next launch
        set_last_workspace(workspace)

    # Load config if specified
    config = None  # type: ignore
    if args.config:
        from berserker.config import load_config

        config = load_config(config_path=args.config, cwd=workspace)

    # Start GUI
    from berserker.gui.app import start_gui_app

    start_gui_app(config=config, session_id=args.session, workspace=workspace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
