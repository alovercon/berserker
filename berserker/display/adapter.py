"""
berserker.display.adapter — Display abstraction layer for CLI/GUI decoupling.

Provides a DisplayAdapter interface that abstracts all output operations
(print, input, selection prompts) so the same conversation logic can work
with both CLI (terminal) and GUI (wxPython) backends.

Python 3.8.10 compatible: uses type comments, no | union syntax.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


class DisplayAdapter(ABC):
    """Abstract interface for display operations.

    Implement this interface to create CLI or GUI-specific display backends.
    The conversation layer calls these methods instead of direct print()/input().
    """

    @abstractmethod
    def display_user_message(self, text):
        # type: (str) -> None
        """Display a user message."""
        pass

    @abstractmethod
    def display_assistant_message(self, text):
        # type: (str) -> None
        """Display an assistant message."""
        pass

    @abstractmethod
    def display_tool_call(self, tool_name, args, result):
        # type: (str, Dict[str, Any], Dict[str, Any]) -> None
        """Display a tool call and its result."""
        pass

    @abstractmethod
    def display_token_usage(self, usage):
        # type: (Dict[str, int]) -> None
        """Display token usage information."""
        pass

    @abstractmethod
    def display_info(self, text):
        # type: (str) -> None
        """Display informational text (welcome messages, help, etc.)."""
        pass

    @abstractmethod
    def display_error(self, text):
        # type: (str) -> None
        """Display an error message."""
        pass

    @abstractmethod
    def request_permission(self, tool_name, args):
        # type: (str, Dict[str, Any]) -> bool
        """Request user permission for tool execution.

        Returns:
            True if allowed, False if denied.
        """
        pass

    @abstractmethod
    def request_selection(self, question, options, allow_custom=False, multiple=False):
        # type: (str, List[Dict[str, str]], bool, bool) -> Optional[Dict[str, Any]]
        """Request user selection from options.

        Args:
            question: The selection question.
            options: List of dicts with 'id', 'label', 'description' keys.
            allow_custom: Whether user can type a custom answer.
            multiple: Whether multiple selections are allowed.

        Returns:
            Dict with 'selected' (list of option ids) and optionally 'custom' (str).
            None if cancelled.
        """
        pass

    @abstractmethod
    def request_input(self, prompt):
        # type: (str) -> Optional[str]
        """Request text input from user.

        Args:
            prompt: The prompt to display.

        Returns:
            User input text, or None if cancelled.
        """
        pass

    @abstractmethod
    def display_instructions(self, summary):
        # type: (List[str]) -> None
        """Display loaded instruction file summaries."""
        pass

    @abstractmethod
    def display_agent_list(self, agents, current_agent):
        # type: (List[Dict[str, str]], str) -> None
        """Display available agents and current selection."""
        pass

    @abstractmethod
    def display_skills(self, skills):
        # type: (List[Dict[str, str]]) -> None
        """Display available skills."""
        pass


class CLIDisplayAdapter(DisplayAdapter):
    """CLI implementation of DisplayAdapter using print()/input()."""

    def display_user_message(self, text):
        # type: (str) -> None
        print("You: {}".format(text))

    def display_assistant_message(self, text):
        # type: (str) -> None
        print("Assistant: {}".format(text))

    def display_tool_call(self, tool_name, args, result):
        # type: (str, Dict[str, Any], Dict[str, Any]) -> None
        # Format args summary
        args_str = ""
        if args:
            args_repr = repr(args)
            if len(args_repr) > 100:
                args_repr = args_repr[:97] + "..."
            args_str = " args: {}".format(args_repr)

        print("[Tool: {}]{}".format(tool_name, args_str))

        # For bash tool, display full output with visual isolation markers
        if tool_name == "bash":
            output = result.get("output", "")
            metadata = result.get("metadata", {})
            was_truncated = metadata.get("truncated", False)

            if result.get("error"):
                result_summary = "Error: {}".format(result.get("error", "Unknown error"))
            elif output:
                output_len = len(output)
                if output_len > 500:
                    separator = "\u250c" + "\u2500" * 59
                    mid_separator = "\u251c" + "\u2500" * 59
                    end_separator = "\u2514" + "\u2500" * 59
                    print(separator)
                    print("\u2502 [bash output: {} chars]".format(output_len))
                    print(mid_separator)
                    print(output)
                    if was_truncated:
                        print("")
                        print("[Output truncated \u2014 see full output in session history]")
                    print(end_separator)
                else:
                    print(output)
                    if was_truncated:
                        print("[Output truncated \u2014 see full output in session history]")
            else:
                result_summary = "(no output)"
            return

        # For non-bash tools
        if result.get("error"):
            result_summary = "Error: {}".format(result.get("error", "Unknown error"))
        else:
            output = result.get("output", "")
            title = result.get("title", "")
            if title:
                result_summary = "{}".format(title)
            elif output:
                if len(output) > 200:
                    result_summary = output[:197] + "..."
                else:
                    result_summary = output
            else:
                result_summary = "(no output)"

        print("[Tool: {}] result: {}".format(tool_name, result_summary))

    def display_token_usage(self, usage):
        # type: (Dict[str, int]) -> None
        if usage:
            print(
                "Tokens: {} in / {} out / {} total".format(
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("total_tokens", 0),
                )
            )
        else:
            print("Tokens: N/A")

    def display_info(self, text):
        # type: (str) -> None
        print(text)

    def display_error(self, text):
        # type: (str) -> None
        print("Error: {}".format(text))

    def request_permission(self, tool_name, args):
        # type: (str, Dict[str, Any]) -> bool
        print("")
        print("=" * 60)
        print("Permission Request: {} wants to use '{}'".format("agent", tool_name))
        print("-" * 60)
        if args:
            for key, value in args.items():
                val_str = str(value)
                if len(val_str) > 100:
                    val_str = val_str[:97] + "..."
                print("  {}: {}".format(key, val_str))
        print("-" * 60)
        try:
            response = input("Allow? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("")
            return False

        if response in ("n", "no"):
            return False
        return True

    def request_selection(self, question, options, allow_custom=False, multiple=False):
        # type: (str, List[Dict[str, str]], bool, bool) -> Optional[Dict[str, Any]]
        """CLI selection: display numbered options and read input."""
        print("")
        print(question)
        print("")
        for i, opt in enumerate(options, 1):
            label = opt.get("label", "")
            desc = opt.get("description", "")
            if desc:
                print("  {}. {} — {}".format(i, label, desc))
            else:
                print("  {}. {}".format(i, label))
        print("")

        if allow_custom:
            prompt = "Select (1-{}) or type custom answer: ".format(len(options))
        else:
            prompt = "Select (1-{}): ".format(len(options))

        try:
            choice = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return None

        if not choice:
            return None

        # Check if numeric selection
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                selected = [options[idx]["id"]] if not multiple else [options[idx]["id"]]
                return {"selected": selected}
            else:
                print("Invalid selection.")
                return None
        elif allow_custom:
            return {"selected": [], "custom": choice}
        else:
            print("Invalid input.")
            return None

    def request_input(self, prompt):
        # type: (str) -> Optional[str]
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    def display_instructions(self, summary):
        # type: (List[str]) -> None
        if summary:
            print("")
            print("Instructions loaded:")
            for item in summary:
                print("  - {}".format(item))
            print("")

    def display_agent_list(self, agents, current_agent):
        # type: (List[Dict[str, str]], str) -> None
        print("")
        print("Available agents:")
        for agent in agents:
            current_marker = " (active)" if agent.get("name") == current_agent else ""
            print(
                "  {} — {} [mode: {}]{}".format(
                    agent.get("name", ""),
                    agent.get("description", ""),
                    agent.get("mode", ""),
                    current_marker,
                )
            )
            print("    Tools: {}".format(", ".join(agent.get("tools", []))))
        print("")
        print("Current agent: {}".format(current_agent))
        print("Usage: /agent <name>  (e.g., /agent plan)")
        print("")

    def display_skills(self, skills):
        # type: (List[Dict[str, str]]) -> None
        if not skills:
            print("")
            print("No skills available.")
            print("Skills can be installed in:")
            print("  - Local project: .berserker/skills/")
            print("  - Global config: ~/.config/berserker/skills/")
            print("")
            return

        print("")
        print("Available skills:")
        print("")
        for skill in skills:
            name = skill.get("name", "unknown")
            desc = skill.get("description", "")
            if desc:
                print("  {} \u2014 {}".format(name, desc))
            else:
                print("  {}".format(name))
        print("")
        print("Use /skill <name> to load a specific skill.")
        print("")
