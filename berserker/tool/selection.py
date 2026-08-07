"""
Selection tool for berserker.

Provides interactive user selection from multiple options with support for:
- Single selection (pick one)
- Multiple selection (pick many)
- Custom text input
- Required selection (must pick from list)
- GUI-compatible callback mode (non-blocking)
- CLI-compatible direct input mode (blocking)

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import sys
from typing import List, Dict, Any, Optional, Callable

from berserker.tool.base import Tool, ToolConfig, ToolContext, ToolResult


# ---------------------------------------------------------------------------
# SelectionTool
# ---------------------------------------------------------------------------


class SelectionTool(Tool):
    """Interactive selection tool for LLM agents.

    Presents options to the user and collects their selection.
    Supports single/multiple choice and custom text input.

    Two modes of operation:
    1. CLI mode (default): Uses print()/input() directly - blocks until user responds
    2. GUI mode: Uses selection_callback from ctx.extra - returns pending result,
       GUI handles display and resumes with user selection
    """

    @property
    def config(self):
        # type: () -> ToolConfig
        return ToolConfig(timeout=120, max_output_tokens=4096)

    def __init__(self):
        # type: () -> None
        super(SelectionTool, self).__init__(
            id="selection",
            description=(
                "Present a set of choices to the user (human) and collect their response interactively. "
                "Use ONLY when you genuinely need human input to make a decision \u2014 "
                "do NOT use this for information you already have or decisions you can make autonomously."
                "\n"
                "Common scenarios:"
                "\n- Confirm with the user before a destructive action"
                "\n- Ask which approach/file/option the user prefers"
                "\n- Let the user choose from multiple proposed solutions"
                "\n- Collect custom text input or feedback"
                "\n"
                "Modes:"
                "\n- single: user picks exactly one option"
                "\n- multiple: user can pick zero or more options"
                "\n- custom: user can type a free-form answer (enabled by default)"
                "\n- required: user must pick from the list (disables custom input)"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user",
                    },
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["label"],
                        },
                        "description": "Available choices",
                    },
                    "multiple": {
                        "type": "boolean",
                        "description": "Allow selecting multiple options (default: false)",
                    },
                    "allow_custom": {
                        "type": "boolean",
                        "description": "Allow user to type custom input (default: true)",
                    },
                    "required": {
                        "type": "boolean",
                        "description": "Must select from options (no custom input allowed, overrides allow_custom)",
                    },
                },
                "required": ["question", "options"],
                "additionalProperties": False,
            },
        )

    def execute(self, args, ctx):
        # type: (Dict[str, Any], ToolContext) -> ToolResult
        """Execute selection interaction."""
        question = args.get("question", "")
        options = args.get("options", [])
        multiple = args.get("multiple", False)
        allow_custom = args.get("allow_custom", True)
        required = args.get("required", False)

        # Validate inputs
        if not question:
            return ToolResult(
                title="Selection Error",
                output="Question is required.",
            )

        if not options:
            return ToolResult(
                title="Selection Error",
                output="At least one option is required.",
            )

        # If required=True, disable custom input
        if required:
            allow_custom = False

        # Check if GUI callback mode is available
        selection_callback = None  # type: Optional[Callable]
        if ctx.extra:
            selection_callback = ctx.extra.get("selection_callback")

        if selection_callback:
            # GUI mode: use callback for non-blocking interaction
            return self._gui_select(question, options, multiple, allow_custom, selection_callback)
        else:
            # CLI mode: use direct print()/input()
            try:
                if multiple:
                    return self._multiple_select(question, options, allow_custom)
                else:
                    return self._single_select(question, options, allow_custom)
            except (EOFError, KeyboardInterrupt):
                return ToolResult(
                    title="Selection Cancelled",
                    output="User cancelled the selection.",
                )
            except Exception as e:
                return ToolResult(
                    title="Selection Error",
                    output="Selection failed: {}".format(str(e)),
                )

    def _gui_select(self, question, options, multiple, allow_custom, callback):
        # type: (str, List[Dict[str, Any]], bool, bool, Callable) -> ToolResult
        """GUI mode: invoke callback and return result.

        The callback is expected to:
        1. Display the selection UI to the user
        2. Wait for user selection
        3. Return a dict with 'selected' (list of option ids/indices) and optionally 'custom' (str)

        This method blocks until the callback returns, but the callback itself
        can use async/event-driven patterns to avoid freezing the GUI.
        """
        try:
            result = callback(
                question=question,
                options=options,
                multiple=multiple,
                allow_custom=allow_custom,
            )
        except Exception as e:
            return ToolResult(
                title="Selection Error",
                output="Selection failed: {}".format(str(e)),
            )

        if result is None:
            return ToolResult(
                title="Selection Cancelled",
                output="User cancelled the selection.",
            )

        # Build ToolResult from callback response
        selected = result.get("selected", [])
        custom = result.get("custom")

        if custom and not selected:
            # Custom input only
            return ToolResult(
                title="Selection Result",
                output="Custom input: {}".format(custom),
                metadata={"type": "custom", "value": custom},
            )
        elif custom and selected:
            # Mixed: options + custom input
            labels = []
            for s in selected:
                if isinstance(s, int) and 0 <= s < len(options):
                    labels.append(options[s].get("label", ""))
                elif isinstance(s, dict):
                    labels.append(s.get("label", ""))
                else:
                    labels.append(str(s))

            result_parts = []
            if labels:
                result_parts.append("Selected options: {}".format(", ".join(labels)))
            result_parts.append("Custom input: {}".format(custom))

            return ToolResult(
                title="Selection Result",
                output="\n".join(result_parts),
                metadata={
                    "type": "mixed",
                    "options": labels,
                    "custom": custom,
                },
            )
        elif selected:
            # Option(s) selected
            labels = []
            for s in selected:
                if isinstance(s, int) and 0 <= s < len(options):
                    labels.append(options[s].get("label", ""))
                elif isinstance(s, dict):
                    labels.append(s.get("label", ""))
                else:
                    labels.append(str(s))

            return ToolResult(
                title="Selection Result",
                output="Selected: {}".format(", ".join(labels)),
                metadata={
                    "type": "options" if len(labels) > 1 else "option",
                    "labels": labels,
                },
            )
        else:
            return ToolResult(
                title="Selection Cancelled",
                output="No selection made.",
            )

    def _display_options(self, question, options, allow_custom):
        # type: (str, List[Dict[str, Any]], bool) -> None
        """Display question and options to the user."""
        print("")
        print("## {}".format(question))
        print("")

        for i, option in enumerate(options, 1):
            label = option.get("label", "")
            description = option.get("description", "")
            if description:
                print("  {}. {} - {}".format(i, label, description))
            else:
                print("  {}. {}".format(i, label))

        if allow_custom:
            print("  {}. Type your own answer".format(len(options) + 1))

        print("")

    def _single_select(self, question, options, allow_custom):
        # type: (str, List[Dict[str, Any]], bool) -> ToolResult
        """Handle single selection mode."""
        self._display_options(question, options, allow_custom)

        while True:
            try:
                choice = input(
                    "Select (1-{}): ".format(len(options) + 1 if allow_custom else len(options))
                )
            except (EOFError, KeyboardInterrupt):
                raise

            choice = choice.strip()
            if not choice:
                print("Please enter a number.")
                continue

            # Check if it's a number
            try:
                choice_num = int(choice)
            except ValueError:
                print("Invalid input. Please enter a number.")
                continue

            # Validate range
            max_choice = len(options) + 1 if allow_custom else len(options)
            if choice_num < 1 or choice_num > max_choice:
                print("Please enter a number between 1 and {}.".format(max_choice))
                continue

            # Custom input
            if allow_custom and choice_num == len(options) + 1:
                try:
                    custom_text = input("Your answer: ")
                except (EOFError, KeyboardInterrupt):
                    raise

                return ToolResult(
                    title="Selection Result",
                    output="Custom input: {}".format(custom_text),
                    metadata={"type": "custom", "value": custom_text},
                )

            # Option selection
            selected = options[choice_num - 1]
            return ToolResult(
                title="Selection Result",
                output="Selected: {}".format(selected.get("label", "")),
                metadata={
                    "type": "option",
                    "index": choice_num - 1,
                    "label": selected.get("label", ""),
                    "description": selected.get("description", ""),
                },
            )

    def _multiple_select(self, question, options, allow_custom):
        # type: (str, List[Dict[str, Any]], bool) -> ToolResult
        """Handle multiple selection mode."""
        self._display_options(question, options, allow_custom)

        print("Enter numbers separated by commas (e.g., 1,3,5)")
        print("")

        while True:
            try:
                choice = input(
                    "Select (1-{}): ".format(len(options) + 1 if allow_custom else len(options))
                )
            except (EOFError, KeyboardInterrupt):
                raise

            choice = choice.strip()
            if not choice:
                print("Please enter at least one number.")
                continue

            # Parse comma-separated numbers
            parts = [p.strip() for p in choice.split(",")]
            selected_indices = []
            has_custom = False

            valid = True
            for part in parts:
                try:
                    num = int(part)
                except ValueError:
                    print("Invalid input: '{}'. Please enter numbers only.".format(part))
                    valid = False
                    break

                max_choice = len(options) + 1 if allow_custom else len(options)
                if num < 1 or num > max_choice:
                    print("Number {} is out of range (1-{}).".format(num, max_choice))
                    valid = False
                    break

                if allow_custom and num == len(options) + 1:
                    has_custom = True
                else:
                    selected_indices.append(num - 1)

            if not valid:
                continue

            # Handle custom input in multiple mode
            if has_custom:
                try:
                    custom_text = input("Your answer: ")
                except (EOFError, KeyboardInterrupt):
                    raise

                # Also include any selected options
                selected_labels = [options[i].get("label", "") for i in selected_indices]
                result_parts = []
                if selected_labels:
                    result_parts.append("Selected options: {}".format(", ".join(selected_labels)))
                result_parts.append("Custom input: {}".format(custom_text))

                return ToolResult(
                    title="Selection Result",
                    output="\n".join(result_parts),
                    metadata={
                        "type": "mixed",
                        "options": selected_labels,
                        "custom": custom_text,
                    },
                )

            # Return selected options
            selected = [options[i] for i in selected_indices]
            labels = [s.get("label", "") for s in selected]

            return ToolResult(
                title="Selection Result",
                output="Selected: {}".format(", ".join(labels)),
                metadata={
                    "type": "options",
                    "indices": selected_indices,
                    "labels": labels,
                },
            )
