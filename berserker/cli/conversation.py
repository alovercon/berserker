"""

berserker.cli.conversation — Interactive REPL conversation mode.



Provides a command-line interactive loop with:

- Slash commands (/help, /exit, /clear, /model, /session, /compact)

- Multi-line input via Ctrl+D (EOF)

- Tool call display

- Session management

- Graceful Ctrl+C handling

- Windows console encoding support



Python 3.8.10 compatible: uses type comments, no match/case,

no str.removeprefix(), no sys.stdout.reconfigure().

"""



from __future__ import annotations



try:

    import readline

except ImportError:

    readline = None  # type: ignore[assignment]



import glob as glob_module

import io

import logging

import os

import signal

import sys

from typing import Any, Dict, List, Optional



from berserker import __version__

from berserker.display.adapter import CLIDisplayAdapter

from berserker.agent.manager import agent_manager

from berserker.logging_config import get_logger, setup_logging

from berserker.provider.base import (

    FINISH_CONTENT_FILTER,

    FINISH_ERROR,

    FINISH_LENGTH,

    FINISH_REFUSAL,

    FINISH_STOP,

    FINISH_TOOL_CALLS,

    AuthenticationError,

    ChatMessage,

    ModelNotFoundError,

    ProviderError,

)

from berserker.provider.registry import registry as provider_registry

from berserker.session.history import command_history

from berserker.session.instruction import instruction_loader

from berserker.session.manager import session_manager

from berserker.session.message_utils import chat_messages_from_session

from berserker.tool.registry import registry as tool_registry

from berserker.workspace import get_workspace, set_workspace



from berserker.command.registry import command_registry



logger = logging.getLogger(__name__)

from berserker.session.token_counter import TokenCounter

from berserker.storage import get_db

from berserker.tool.init import register_default_tools



# ---------------------------------------------------------------------------

# Console Encoding Setup

# ---------------------------------------------------------------------------





def _setup_console_encoding():

    # type: () -> None

    """Attempt to set stdout/stderr to UTF-8 on Windows.



    On Windows, the default console encoding may be cp1252 or GBK.

    We try to wrap stdout/stderr with UTF-8 encoding, falling back

    gracefully if the console does not support it.

    """

    if sys.platform != "win32":

        return



    try:

        # Try to reconfigure stdout/stderr to UTF-8

        # Python 3.7+ supports buffer attribute on TextIOWrapper

        if hasattr(sys.stdout, "buffer"):

            sys.stdout = io.TextIOWrapper(

                sys.stdout.buffer,

                encoding="utf-8",

                errors="replace",

                line_buffering=True,

            )

        if hasattr(sys.stderr, "buffer"):

            sys.stderr = io.TextIOWrapper(

                sys.stderr.buffer,

                encoding="utf-8",

                errors="replace",

                line_buffering=True,

            )

    except (AttributeError, OSError, ValueError):

        # Fallback: leave encoding as-is

        pass





# ---------------------------------------------------------------------------

# Signal Handling

# ---------------------------------------------------------------------------



# Global flag to track if we are currently reading input

_interrupt_during_input = False  # type: bool



# Current active agent name (defaults to 'build')

_current_agent = "berserker"  # type: str



# Current active SessionContext for abort coordination

_current_ctx = None  # type: Optional[Any]





def _sigint_handler(signum, frame):

    # type: (int, Any) -> None

    """Handle Ctrl+C during input.



    Sets a flag so the input loop can respond gracefully.

    If an agent is running, also signals abort via SessionContext.

    """

    global _interrupt_during_input

    _interrupt_during_input = True



    # Signal abort to active session context if agent is running

    global _current_ctx

    if _current_ctx is not None:

        try:

            _current_ctx.request_abort()

        except Exception as e:

            logger.warning("Failed to request abort on session context: %s", e)

            pass

    # Print a newline so the prompt appears on a fresh line

    print("")





def _setup_signal_handlers():

    # type: () -> None

    """Register SIGINT handler for graceful Ctrl+C handling."""

    try:

        signal.signal(signal.SIGINT, _sigint_handler)

    except (ValueError, OSError):

        # Signal handling may not work in all contexts (e.g., non-main thread)

        pass





# ---------------------------------------------------------------------------

# Slash Command Handlers

# ---------------------------------------------------------------------------





def _cmd_help(plugin_commands=None):

    # type: (Optional[Dict[str, Dict[str, Any]]]) -> None

    """Print available slash commands."""

    print(command_registry.get_help_text())





def _cmd_agent(agent_name=None, session_id=None):

    # type: (Optional[str], Optional[str]) -> str

    """Switch the active agent or list available agents.



    Args:

        agent_name: Agent name to switch to. If None, lists available agents.

        session_id: Current session ID for persisting agent config.



    Returns:

        The current agent name after operation.

    """

    global _current_agent



    if not agent_name:

        # List available agents

        print("")

        print("Available agents:")

        for agent in agent_manager.list():

            current_marker = " (active)" if agent.name == _current_agent else ""

            print("  {} — {} [mode: {}]".format(agent.name, agent.description or "", agent.mode))

            print("    Tools: {}".format(", ".join(agent.tools)))

        print("")

        print("Current agent: {}".format(_current_agent))

        print("Usage: /agent <name>  (e.g., /agent plan)")

        print("")

        return _current_agent



    # Try to switch to the specified agent

    try:

        agent = agent_manager.get(agent_name)

        _current_agent = agent.name

        print("Switched to agent: {} ({})".format(agent.name, agent.description or ""))

        print("  Mode: {}".format(agent.mode))

        print("  Tools: {}".format(", ".join(agent.tools)))

        print("  Permission: {}".format(agent.permission))



        # Persist agent config to session

        if session_id is not None:

            try:

                session_manager.save_agent_config(session_id, _current_agent)

            except Exception as exc:

                logging.getLogger(__name__).debug("Failed to save agent config: %s", exc)



        # Re-scan instructions on agent switch (primary agents get instructions)

        if agent.mode == "primary":

            _scan_instructions()



        return _current_agent

    except KeyError:

        print("Error: Agent '{}' not found.".format(agent_name))

        print(

            "Available agents: {}".format(", ".join(sorted([a.name for a in agent_manager.list()])))

        )

        return _current_agent





def _find_plan(plan_name):

    # type: (str) -> Optional[str]

    """Find a plan file by name in .omo/plans/ directory.



    Args:

        plan_name: Plan name (with or without .md extension).



    Returns:

        Absolute path to the plan file, or None if not found.

    """

    from berserker.workspace import get_workspace



    workspace = get_workspace()

    plans_dir = os.path.join(workspace, ".omo", "plans")



    if not os.path.isdir(plans_dir):

        return None



    # Try exact name first

    if plan_name.endswith(".md"):

        candidate = os.path.join(plans_dir, plan_name)

        if os.path.isfile(candidate):

            return candidate

    else:

        candidate = os.path.join(plans_dir, plan_name + ".md")

        if os.path.isfile(candidate):

            return candidate



    # Try partial match

    for fname in os.listdir(plans_dir):

        if fname.endswith(".md") and plan_name.lower() in fname.lower():

            return os.path.join(plans_dir, fname)



    return None





def _cmd_start_work(plan_name, session_id, display_adapter=None):

    # type: (str, str, Optional[CLIDisplayAdapter]) -> str

    """Start executing a plan via the executor agent.



    Args:

        plan_name: Plan name or path.

        session_id: Current session ID.



    Returns:

        The current session ID.

    """

    global _current_agent



    if not plan_name:

        print("")

        print("Usage: /start-work <plan-name>")

        print("  plan-name: Name of a plan in .omo/plans/ (with or without .md)")

        print("")

        # List available plans

        from berserker.workspace import get_workspace



        workspace = get_workspace()

        plans_dir = os.path.join(workspace, ".omo", "plans")

        if os.path.isdir(plans_dir):

            plans = [f for f in os.listdir(plans_dir) if f.endswith(".md")]

            if plans:

                print("Available plans:")

                for p in sorted(plans):

                    print("  {}".format(p))

                print("")

        else:

            print("No .omo/plans/ directory found.")

            print("")

        return session_id



    # Find the plan file

    plan_path = _find_plan(plan_name)

    if plan_path is None:

        print("Error: Plan '{}' not found.".format(plan_name))

        print("Use /start-work without arguments to list available plans.")

        return session_id



    # Read the plan content

    try:

        with open(plan_path, "r", encoding="utf-8") as f:

            plan_content = f.read()

    except Exception as exc:

        print("Error: Failed to read plan file: {}".format(exc))

        return session_id



    # Verify executor agent exists

    try:

        agent_manager.get("executor")

    except KeyError:

        print("Error: 'executor' agent is not configured.")

        return session_id



    # Auto-switch to executor if not already on it

    original_agent = _current_agent

    if _current_agent != "executor":

        print("")

        print("-> Current agent is '{}', switching to 'executor'".format(_current_agent))

        _current_agent = "executor"

        # Persist agent config to session

        try:

            session_manager.save_agent_config(session_id, _current_agent)

        except Exception as exc:

            logging.getLogger(__name__).debug("Failed to save agent config: %s", exc)



    # Build the execution message

    plan_basename = os.path.basename(plan_path)

    exec_message = "Execute the following plan from '{}':\n\n{}".format(plan_basename, plan_content)



    # Save user message to session via SessionContext (ensures cache invalidation)

    try:

        _current_ctx.append_message("user", exec_message)

    except Exception as exc:

        print("Error saving message: {}".format(exc))

        # Revert agent switch on failure

        if original_agent != _current_agent:

            _current_agent = original_agent

        return session_id



    # Build messages and execute

    messages = _build_messages_from_session(session_id)

    _execute_agent_turn(messages, session_id, display_adapter)



    return session_id





def _cmd_clear(current_session_id):  # type: (str) -> str

    """Clear conversation history by creating a new session.



    Args:

        current_session_id: The current session ID.



    Returns:

        The new session ID.

    """

    global _current_ctx

    global _current_agent

    new_session_id = session_manager.create()

    _current_ctx = session_manager.switch_to(new_session_id)

    session_manager.save_agent_config(new_session_id, _current_agent)

    print("Cleared conversation history. New session: {}".format(new_session_id))

    return new_session_id





def _cmd_model(model_spec):

    # type: (str) -> Optional[str]

    """Switch the model for the current conversation.



    Args:

        model_spec: Model specification in format 'provider/model' or just 'model'.



    Returns:

        The model name if successfully switched, None otherwise.

    """

    if not model_spec:

        print("Error: Please specify a model. Usage: /model <provider/model>")

        return None



    # Try to find the model in registry

    try:

        provider, model_name = provider_registry.get_provider_for_model(model_spec)

        print("Switched to model: {} (provider: {})".format(model_name, provider.id))

        return model_name

    except ModelNotFoundError:

        # Model not found in registry — store it anyway for config-based lookup

        print(

            "Warning: Model '{}' not found in registry. Will attempt to use it.".format(model_spec)

        )

        return model_spec

    except ProviderError as exc:

        print("Error: {}".format(exc))

        return None





def _cmd_session(session_id):

    # type: (str) -> Optional[str]

    """Switch to a different session.



    Args:

        session_id: The session ID to switch to.



    Returns:

        The session ID if successfully switched, None otherwise.

    """

    global _current_ctx

    global _current_agent

    if not session_id:

        # List available sessions when no ID is specified

        sessions = session_manager.list_sessions()

        if not sessions:

            print("No sessions available. Create a session first.")

            return None

        print("Available sessions:")

        for i, s in enumerate(sessions, 1):

            sid = s.get("id", "")

            title = s.get("title", "")

            msg_count = len(s.get("messages", []))

            created = s.get("created_at", "")

            current_sid = getattr(_current_ctx, "session_id", None) if _current_ctx else None

            marker = " (current)" if sid == current_sid else ""

            print("  {}. {}{}  [{}]  Messages: {}".format(i, sid, marker, title, msg_count))

        print("\nUsage: /session <id>")

        return None



    session_data = session_manager.load(session_id)

    if session_data is None:

        print("Error: Session '{}' not found.".format(session_id))

        return None



    # Switch to the new session context

    _current_ctx = session_manager.switch_to(session_id)



    # Restore agent config for this session

    agent_config = session_manager.load_agent_config(session_id)

    if agent_config is not None:

        saved_agent = agent_config.get("agent_name")

        if saved_agent:

            try:

                agent_manager.get(saved_agent)

                _current_agent = saved_agent

                print("  Agent: {} (restored from session)".format(_current_agent))

            except KeyError:

                print(

                    "  Warning: Saved agent '{}' not found, using current agent".format(saved_agent)

                )



    print("Switched to session: {}".format(session_id))

    if session_data.get("title"):

        print("  Title: {}".format(session_data["title"]))

    msg_count = len(session_data.get("messages", []))

    print("  Messages: {}".format(msg_count))

    return session_id





def _scan_instructions():

    # type: () -> None

    """Scan for and display AGENTS.md instruction file findings.



    Called on session start and agent switch to inform user about

    loaded project/global instructions. Uses the global workspace

    directory for instruction file discovery.

    """

    from berserker.workspace import get_workspace



    try:

        summary = instruction_loader.scan(workdir=get_workspace())

        if summary:

            print("")

            print("Instructions loaded:")

            for item in summary:

                print("  - {}".format(item))

            print("")

    except Exception as exc:

        logging.getLogger(__name__).debug("Failed to scan instructions: {}".format(exc))





def _cmd_init(user_args="", current_session_id=None, display_adapter=None):

    # type: (str, Optional[str], Optional[CLIDisplayAdapter]) -> str

    """Generate or update AGENTS.md for the current workspace.



    Sends a prompt to the LLM to autonomously analyze the workspace and

    generate an AGENTS.md file. The LLM explores files as needed.



    Args:

        user_args: Optional user-provided focus or constraints.

        current_session_id: The current session ID to return.

        display_adapter: Optional CLIDisplayAdapter for displaying output.



    Returns:

        The current session ID.

    """

    from berserker.agent.init_prompt import build_init_prompt, load_existing_agents_md

    from berserker.provider.base import ChatMessage



    workspace = get_workspace()

    print("")

    print("Initializing AGENTS.md for workspace: {}".format(workspace))

    print("")



    user_focus = user_args.strip() if user_args else ""

    agents_md_path = os.path.join(workspace, "AGENTS.md")

    existing_agents_md = load_existing_agents_md(workspace)



    prompt = build_init_prompt(

        user_focus=user_focus,

        analysis_text=None,  # No pre-scan; let LLM explore autonomously

        existing_agents_md=existing_agents_md,

        agents_md_path=agents_md_path,

    )

    # Execute the agent to generate AGENTS.md content

    print("Generating AGENTS.md content...")

    print("")



    # Build messages

    messages = [ChatMessage(role="user", content=prompt)]



    # Callback to display tool calls

    def _on_tool_call(tool_name, args, result):

        # type: (str, Dict[str, Any], Dict[str, Any]) -> None

        if display_adapter:

            display_adapter.display_tool_call(tool_name, args, result)



    # Callback for interactive permission requests (ask mode)

    def _on_permission_ask(tool_name, args):

        # type: (str, Dict[str, Any]) -> bool

        """Prompt user for permission before executing a tool in ask mode."""

        print("")

        print("=" * 60)

        print("Permission Request: {} wants to use '{}'".format(_current_agent, tool_name))

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



    # We need a temporary session for this operation to avoid polluting the main session

    init_session_id = session_manager.create()

    sid = current_session_id or init_session_id



    try:

        result = agent_manager.execute(

            _current_agent,

            messages,

            init_session_id,

            tool_registry,

            on_tool_call=_on_tool_call,

            on_permission_ask=_on_permission_ask,

        )

    except Exception as exc:

        print("Error: Failed to generate AGENTS.md: {}".format(exc))

        return sid



    # Extract response content

    response_content = result.get("content", "")



    if not response_content:

        print("Error: LLM returned empty content. AGENTS.md was not created.")

        return sid



    # Write AGENTS.md to workspace

    print("")

    print("Writing AGENTS.md to {}...".format(agents_md_path))



    try:

        with open(agents_md_path, "w", encoding="utf-8") as f:

            f.write(response_content)

        print("AGENTS.md created successfully ({} characters).".format(len(response_content)))

    except Exception as exc:

        print("Error: Failed to write AGENTS.md: {}".format(exc))

        return sid



    print("")

    return sid





def _cmd_compact(session_id):

    # type: (str) -> None

    """Trigger context compression for the current session.



    Args:

        session_id: The current session ID.

    """

    global _current_ctx

    try:

        # Use SessionContext if available, otherwise fall back to manager

        if _current_ctx is not None:

            messages = _current_ctx.get_messages()

        else:

            messages = session_manager.get_messages(session_id)



        chat_messages = []  # type: List[ChatMessage]

        for msg in messages:

            chat_messages.append(

                ChatMessage(

                    role=msg.get("role", "user"),

                    content=msg.get("content", ""),

                    tool_calls=msg.get("tool_calls"),

                    tool_call_id=msg.get("tool_call_id") or msg.get("tool_result_for"),

                )

            )



        # Count tokens before compaction

        token_counter = TokenCounter()

        tokens_before = token_counter.count_messages(chat_messages, model="gpt-4o")



        compacted = agent_manager.compact(chat_messages)



        # Persist compacted messages to DB (fixes no-op bug)

        if len(compacted) < len(chat_messages):

            # Archive the originals before replacing (recoverable history)

            try:

                session_manager.archive_messages(session_id, reason="manual-compact")

            except Exception:

                pass

            # Convert ChatMessage objects to dicts for replace_messages

            compacted_dicts = []

            for msg in compacted:

                msg_dict = {

                    "role": msg.role,

                    "content": msg.content,

                    "tool_calls": msg.tool_calls,

                    "tool_result_for": msg.tool_call_id,

                }

                compacted_dicts.append(msg_dict)



            if _current_ctx is not None:

                inserted = (

                    _current_ctx._manager.replace_messages(session_id, compacted_dicts)

                    if _current_ctx._manager

                    else 0

                )

                _current_ctx.refresh()

            else:

                inserted = session_manager.replace_messages(session_id, compacted_dicts)



            # Count tokens after compaction

            tokens_after = token_counter.count_messages(compacted, model="gpt-4o")

            tokens_freed = tokens_before - tokens_after



            # Get pruning stats

            db = get_db()

            pruning_cursor = db.execute(

                "SELECT COUNT(*) as pruned_count, SUM(original_content_length - pruned_content_length) as tokens_freed FROM pruning_state WHERE session_id = ?",

                [session_id],

            )

            pruning_row = pruning_cursor.fetchone()

            messages_pruned = pruning_row[0] if pruning_row and pruning_row[0] else 0

            pruning_tokens_freed = pruning_row[1] if pruning_row and pruning_row[1] else 0



            # Get snapshot stats

            snapshot_cursor = db.execute(

                "SELECT COUNT(*) as snapshot_count, SUM(additions) as total_additions, SUM(deletions) as total_deletions, SUM(files) as total_files FROM session_snapshots WHERE session_id = ?",

                [session_id],

            )

            snapshot_row = snapshot_cursor.fetchone()

            snapshots_created = snapshot_row[0] if snapshot_row and snapshot_row[0] else 0

            total_additions = snapshot_row[1] if snapshot_row and snapshot_row[1] else 0

            total_deletions = snapshot_row[2] if snapshot_row and snapshot_row[2] else 0

            total_files = snapshot_row[3] if snapshot_row and snapshot_row[3] else 0



            # Display enhanced statistics

            print(

                "Context compressed: {} messages -> {} messages ({} persisted)".format(

                    len(chat_messages), len(compacted), inserted

                )

            )

            print(

                "Tokens: {} -> {} (freed {} tokens)".format(

                    tokens_before, tokens_after, tokens_freed

                )

            )

            print(

                "Pruning: {} messages pruned, {} tokens freed".format(

                    messages_pruned, pruning_tokens_freed

                )

            )

            print(

                "Snapshots: {} created, {} files changed (+{}/-{} lines)".format(

                    snapshots_created, total_files, total_additions, total_deletions

                )

            )

        else:

            print("Context unchanged: {} messages (within token budget)".format(len(chat_messages)))

    except Exception as exc:

        print("Error during compaction: {}".format(exc))





def _summarize_tool_output(tool_name, args, result):

    # type: (str, Dict[str, Any], Dict[str, Any]) -> str

    """Generate a concise summary of tool execution for display.



    If output is too long, truncates with a summary indicator.



    Args:

        tool_name: Name of the tool that was called.

        args: Arguments passed to the tool.

        result: Result dictionary from the tool execution.



    Returns:

        Summary string for display.

    """

    if result.get("error"):

        return "Error: {}".format(result.get("error", "Unknown error"))



    output = result.get("output", "")

    title = result.get("title", "")



    # Tool-specific summaries

    if tool_name == "read":

        file_path = args.get("file_path", "")

        line_count = output.count("\n") + 1 if output else 0

        return "Read {} ({} lines)".format(file_path, line_count)



    if tool_name == "write":

        file_path = args.get("file_path", "")

        char_count = len(args.get("content", ""))

        return "Wrote {} ({} chars)".format(file_path, char_count)



    if tool_name == "ls":

        path = args.get("path", ".")

        entry_count = len(output.strip().split("\n")) if output.strip() else 0

        return "Listed {} ({} entries)".format(path, entry_count)



    if tool_name == "glob":

        pattern = args.get("pattern", "")

        match_count = len(output.strip().split("\n")) if output.strip() else 0

        return "Glob '{}' ({} matches)".format(pattern, match_count)



    if tool_name == "grep":

        pattern = args.get("pattern", "")

        path = args.get("path", "")

        match_count = len(output.strip().split("\n")) if output.strip() else 0

        return "Grep '{}' in {} ({} matches)".format(pattern, path, match_count)



    if tool_name == "bash":

        command = args.get("command", "")

        # Truncate long commands

        if len(command) > 60:

            command = command[:57] + "..."

        exit_code = result.get("metadata", {}).get("exit_code", "?")

        return "Bash: {} (exit: {})".format(command, exit_code)



    if tool_name == "edit":

        file_path = args.get("file_path", "")

        return "Edited {}".format(file_path)



    if tool_name == "skill":

        skill_name = args.get("skill_name", "")

        if title:

            return "Loaded skill: {}".format(skill_name)

        return "Skill '{}' not found".format(skill_name)



    if tool_name == "todo":

        operation = args.get("operation", "")

        if operation == "list":

            # Show full todo list in summary

            return output if output else "(no todos)"

        if operation == "add":

            # Show added item + current list

            return output if output else "Added todo"

        if operation == "update":

            return output if output else "Updated todo"

        if operation == "delete":

            return output if output else "Deleted todo"

        if operation == "clear":

            return output if output else "Cleared todos"

        return output if output else "Todo: {}".format(operation)



    # Generic summary

    if title:

        summary = title

    elif output:

        summary = output

    else:

        summary = "(no output)"



    # Truncate long output

    if len(summary) > 200:

        summary = summary[:197] + "..."



    return summary





# ---------------------------------------------------------------------------

# Input Reading

# ---------------------------------------------------------------------------





def _load_readline_history(session_id):

    # type: (str) -> None

    """Clear readline history and load session's command history.



    Args:

        session_id: The session whose command history to load.

    """

    if readline is None:

        return

    try:

        readline.clear_history()  # type: ignore[union-attr]

        for entry in command_history.get(session_id):

            readline.add_history(entry)  # type: ignore[union-attr]

    except Exception as e:

        logger.warning("Failed to load command history for session %s: %s", session_id, e)

        pass





def _read_input():

    # type: () -> Optional[str]

    """Read user input from the console.



    Supports multi-line input via Ctrl+D (EOF).

    On EOF: if input is empty, treat as end of input; otherwise process

    accumulated text.

    On empty input: return empty string (caller should skip).



    Returns:

        The input text, or None to indicate end of conversation.

    """

    global _interrupt_during_input

    _interrupt_during_input = False



    lines = []  # type: List[str]



    while True:

        if _interrupt_during_input:

            _interrupt_during_input = False

            # Ask if user wants to exit

            try:

                answer = input("Exit? (y/n): ")

            except (EOFError, KeyboardInterrupt):

                return None

            if answer.strip().lower() in ("y", "yes"):

                return None

            # Continue the input loop

            continue



        try:

            if not lines:

                # First line — show normal prompt

                line = input("You: ")

            else:

                # Continuation — show continuation prompt

                line = input("... ")

        except EOFError:

            # Ctrl+D pressed

            if not lines:

                # Empty input + EOF = end of conversation

                return None

            # Return accumulated text

            return "\n".join(lines)

        except KeyboardInterrupt:

            # Ctrl+C during input

            _interrupt_during_input = True

            print("")

            continue



        if not lines and not line.strip():

            # Empty input on first line — skip

            return ""



        lines.append(line)



        # If we got a line without EOF, check if it's complete

        # For simplicity, single Enter on first non-empty line sends it

        if lines and line == "":

            # Empty line after content — could be end of multi-line

            # For now, just return what we have

            return "\n".join(lines).strip()



        # Single line input — return immediately

        if len(lines) == 1:

            return lines[0]





def _cmd_skills():

    # type: () -> None

    """List all available skills."""

    from berserker.tool.skill import _list_available_skills



    skills = _list_available_skills()



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





# ---------------------------------------------------------------------------

# Conversation Loop

# ---------------------------------------------------------------------------





def _build_messages_from_session(session_id):

    # type: (str) -> List[ChatMessage]

    """Build a list of ChatMessage objects from session history.



    Uses the active SessionContext if available, otherwise falls back

    to direct session_manager calls.



    Args:

        session_id: The session ID to load messages from.



    Returns:

        List of ChatMessage objects.

    """

    global _current_ctx

    if _current_ctx is not None:

        messages = _current_ctx.get_messages()

    else:

        messages = session_manager.get_messages(session_id)



    # Debug-log tool message resolution

    for msg in messages:

        if msg.get("role") == "tool":

            tcid = msg.get("tool_call_id") or msg.get("tool_result_for")

            logger.debug(

                "[LOAD_MSG] role=tool, tool_call_id=%s, tool_result_for=%s, resolved=%s",

                msg.get("tool_call_id"),

                msg.get("tool_result_for"),

                tcid,

            )



    return chat_messages_from_session(messages)





def _process_slash_command(text, session_id, plugin_commands=None, display_adapter=None):

    # type: (str, str, Optional[Dict[str, Dict[str, Any]]], Optional[CLIDisplayAdapter]) -> Optional[str]

    """Process a slash command.



    Args:

        text: The command text (starting with /).

        session_id: The current session ID.

        plugin_commands: Dict of plugin-registered commands (from plugin_manager.get_registered_commands()).



    Returns:

        New session ID if changed, or None to keep current.

    """

    from berserker.command.processor import (

        CommandResult,

        is_special_command,

        lookup_command,

        parse_command,

    )



    command, arg, _ = parse_command(text)

    if not command:

        return session_id



    # Builtin special cases (must bypass registry)

    special = is_special_command(command)

    if special == CommandResult.EXIT:

        return "__exit__"

    if special == CommandResult.START_WORK:

        return _cmd_start_work(arg, session_id, display_adapter)



    # Normal command: look up in registry

    cmd = lookup_command(command)

    if cmd:

        if cmd.name == "help":

            # /help: get_help_text() already includes all scopes (builtin, user, project, plugin)

            print(command_registry.get_help_text())

            return session_id

        elif cmd.name == "clear":

            return _cmd_clear(session_id)

        elif cmd.name == "session":

            new_sid = _cmd_session(arg)

            if new_sid is not None:

                return new_sid

            return session_id

        elif cmd.name == "init":

            return _cmd_init(arg, session_id, display_adapter)

        elif cmd.name == "compact":

            _cmd_compact(session_id)

            return session_id

        elif cmd.name == "agent":

            _cmd_agent(arg if arg else None, session_id)

            return session_id

        elif cmd.name == "model":

            _cmd_model(arg)

            return session_id

        elif cmd.name == "skills":

            _cmd_skills()

            return session_id

        else:

            # Legacy plugin command (Python handler)

            if plugin_commands and command in plugin_commands:

                cmd_info = plugin_commands[command]

                handler = cmd_info.get("handler")

                if handler is not None:

                    try:

                        result = handler(arg, session_id)

                        return result

                    except Exception as exc:

                        print("Error in plugin command '{}': {}".format(command, exc))

                        return session_id

            # Markdown template command → inject into conversation

            if cmd.template:

                from berserker.command.formatter import (

                    count_required_args,

                    resolve_command_template,

                    validate_command_args,

                )



                valid, error = validate_command_args(cmd, arg)

                if not valid:

                    print(error)

                    return session_id

                resolved = resolve_command_template(cmd, arg)

                return "__TEMPLATE__:" + resolved



    print("Unknown command: {}. Type /help for available commands.".format(command))

    return session_id





def _execute_agent_turn(messages, session_id, display_adapter=None):

    # type: (List[ChatMessage], str, Optional[CLIDisplayAdapter]) -> None

    """Execute a single agent turn and display the response.



    Args:

        messages: List of ChatMessage objects to send.

        session_id: The current session ID.

        display_adapter: Optional CLIDisplayAdapter for displaying output.

    """

    global _current_agent



    # Track whether tool calls were displayed during this turn

    tool_calls_displayed = [False]  # type: List[bool]



    # Callback to display tool calls in real-time

    def _on_tool_call(tool_name, args, result):

        # type: (str, Dict[str, Any], Dict[str, Any]) -> None

        tool_calls_displayed[0] = True

        if display_adapter:

            display_adapter.display_tool_call(tool_name, args, result)



    # Callback for interactive permission requests (ask mode)

    def _on_permission_ask(tool_name, args):

        # type: (str, Dict[str, Any]) -> bool

        """Prompt user for permission before executing a tool in ask mode."""

        print("")

        print("=" * 60)

        print("Permission Request: {} wants to use '{}'".format(_current_agent, tool_name))

        print("-" * 60)

        if args:

            # Format args nicely

            for key, value in args.items():

                # Truncate long values for display

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



    try:

        result = agent_manager.execute(

            _current_agent,

            messages,

            session_id,

            tool_registry,

            on_tool_call=_on_tool_call,

            on_permission_ask=_on_permission_ask,

            # Wire the session abort event so Ctrl+C also flows into the

            # executor's abort checkpoints (and into compaction).

            abort_event=_current_ctx._abort_event if _current_ctx is not None else None,

        )

    except AuthenticationError as exc:

        print("Error: Authentication failed. Check API key for your provider.")

        return

    except ModelNotFoundError as exc:

        print("Error: {}".format(exc))

        return

    except ProviderError as exc:

        print("Error: {}".format(exc))

        return

    except Exception as exc:

        print("Error: Unexpected error: {}".format(exc))

        return



    # Extract response content, usage, and finish_reason from result dict

    response = result.get("content", "")

    usage = result.get(

        "usage",

        {

            "prompt_tokens": 0,

            "completion_tokens": 0,

            "total_tokens": 0,

        },

    )

    finish_reason = result.get("finish_reason", FINISH_STOP)



    # Display assistant response with finish_reason-aware messaging

    if response:

        if display_adapter:

            display_adapter.display_assistant_message(response)

    elif finish_reason == FINISH_TOOL_CALLS:

        pass  # Tool calls will be displayed by the tool execution loop

    elif finish_reason == FINISH_CONTENT_FILTER:

        if display_adapter:

            display_adapter.display_assistant_message("(response blocked by content filter)")

    elif finish_reason == FINISH_LENGTH:

        if display_adapter:

            display_adapter.display_assistant_message("(response truncated: max tokens reached)")

    elif finish_reason == FINISH_REFUSAL:

        if display_adapter:

            display_adapter.display_assistant_message("(model refused to generate response)")

    elif finish_reason == FINISH_ERROR:

        if display_adapter:

            display_adapter.display_assistant_message("(error during generation)")

    elif tool_calls_displayed[0]:

        pass  # Tool calls already displayed, no text response needed

    else:

        if display_adapter:

            display_adapter.display_assistant_message("(empty response)")



    # Display token usage

    if display_adapter:

        display_adapter.display_token_usage(usage)





def start_conversation(config=None, session_id=None, continue_last=False, workspace=None):

    # type: (Optional[Dict[str, Any]], Optional[str], bool, Optional[str]) -> None

    """Start an interactive conversation loop.



    This is the main entry point for the REPL conversation mode.



    Args:

        config: Optional configuration dictionary. If None, loaded from default

                config files.

        session_id: Optional session ID to start with or continue.

        continue_last: If True, attempt to continue the most recent session.

        workspace: Optional workspace directory. If None, defaults to os.getcwd().

    """

    global _current_ctx, _current_agent



    # Initialize global workspace    from berserker.workspace import set_workspace



    if workspace is not None:

        set_workspace(workspace)

    # Setup console encoding and signal handlers

    _setup_console_encoding()

    _setup_signal_handlers()



    # Load config if not provided (needed for logging config)

    if config is None:

        from berserker.config import load_config

        from berserker.workspace import get_workspace



        config = load_config(config_path=None, cwd=get_workspace())



    # Initialize logging with config settings

    logging_config = config.get("logging", {})  # type: Dict[str, Any]

    log_enabled = logging_config.get("enabled", True)

    log_file = logging_config.get("file")

    log_level = logging_config.get("level")



    # Convert string level to logging constant if needed

    file_level = None  # type: Optional[int]

    console_level = None  # type: Optional[int]

    if log_level is not None:

        if isinstance(log_level, str):

            level_map = {

                "DEBUG": logging.DEBUG,

                "INFO": logging.INFO,

                "WARNING": logging.WARNING,

                "ERROR": logging.ERROR,

                "CRITICAL": logging.CRITICAL,

            }

            file_level = level_map.get(log_level.upper())

            console_level = file_level

        else:

            file_level = log_level

            console_level = log_level



    setup_logging(

        log_file=log_file, file_level=file_level, console_level=console_level, enabled=log_enabled

    )



    logger = get_logger(__name__)

    if log_enabled:

        logger.info("berserker %s starting conversation mode", __version__)



    # Ensure config is loaded (type narrowing for type checker)

    assert config is not None, "Config must be loaded before proceeding"



    # Load providers from config into registry

    provider_registry.load_from_config(config)



    # Load agent overrides from config

    from berserker.agent.manager import agent_manager



    agent_manager.load_from_config(config)



    # Load ticket processing team configs

    ticket_configs = glob_module.glob(

        os.path.join(os.path.dirname(__file__), "..", "agent", "configs", "ticket-*.json")

    )



    # Get primary model to use as fallback for ticket agents

    primary_model = None

    try:

        build_agent = agent_manager.get("berserker")

        primary_model = build_agent.model

    except KeyError:

        pass



    for config_file in sorted(ticket_configs):

        try:

            # Read and patch model if gpt-4o is not available

            import json



            with open(config_file, "r", encoding="utf-8") as f:

                config_data = json.load(f)



            if primary_model and "agents" in config_data:

                for agent_cfg in config_data["agents"]:

                    if agent_cfg.get("model") == "gpt-4o":

                        # Check if gpt-4o is actually available

                        try:

                            provider_registry.get_provider_for_model("gpt-4o")

                        except Exception:

                            agent_cfg["model"] = primary_model



            # Write patched config to temp file and load

            import tempfile



            with tempfile.NamedTemporaryFile(

                mode="w", suffix=".json", delete=False, encoding="utf-8"

            ) as tmp:

                json.dump(config_data, tmp)

                tmp_path = tmp.name

            count = agent_manager.load_from_file(tmp_path)

            os.unlink(tmp_path)

        except Exception as e:

            print("[TICKET] Failed to load ticket config {}: {}".format(config_file, e))



    # Load permission overrides from config

    from berserker.permission import permission_checker



    permission_checker.load_from_config(config)



    # Load and activate plugins from config

    from berserker.plugin_system import plugin_manager



    plugin_manager.load_from_config(config)

    plugin_manager.activate_all()



    # Log plugin activation status

    active_plugins = plugin_manager.list_plugins()

    if active_plugins:

        plugin_names = ", ".join(p["name"] for p in active_plugins)

        print("Plugins loaded: {}".format(plugin_names))

        if log_enabled:

            logger.info("Plugins activated: %s", plugin_names)



    # Scan all command sources (builtin + user + project + plugin)

    command_registry.scan(get_workspace())



    # Register all tools

    register_default_tools(tool_registry)



    # Determine session to use

    current_session_id = session_id  # type: Optional[str]



    if continue_last:

        # Try to get the most recent session

        sessions = session_manager.list_sessions(limit=1)

        if sessions:

            current_session_id = sessions[0]["id"]

            print("Continuing last session: {}".format(current_session_id))

        else:

            current_session_id = session_manager.create()

            session_manager.save_agent_config(current_session_id, _current_agent)

            print("No previous sessions found. Created new session: {}".format(current_session_id))

    elif current_session_id is None:

        # Create a new session

        current_session_id = session_manager.create()

        session_manager.save_agent_config(current_session_id, _current_agent)



    # Verify session exists (if specified)

    if current_session_id is not None:

        session_data = session_manager.load(current_session_id)

        if session_data is None:

            print("Error: Session '{}' not found. Creating new session.".format(current_session_id))

            current_session_id = session_manager.create()

            session_manager.save_agent_config(current_session_id, _current_agent)

    # At this point current_session_id is guaranteed to be set

    assert current_session_id is not None, "Session ID should have been created"



    # Activate session context for message operations and abort coordination

    global _current_ctx

    _current_ctx = session_manager.switch_to(current_session_id)



    # Restore agent config for this session (continue_last or explicit session)

    agent_config = session_manager.load_agent_config(current_session_id)

    if agent_config is not None:

        saved_agent = agent_config.get("agent_name")

        if saved_agent:

            try:

                agent_manager.get(saved_agent)

                _current_agent = saved_agent

            except KeyError:

                pass  # Saved agent no longer exists, keep default



    # Print welcome message    print("")

    print("berserker {} (beta) — Type /help for commands, Ctrl+D to send".format(__version__))

    print("Session: {}".format(current_session_id))

    print(

        "Agent: {} ({})".format(_current_agent, agent_manager.get(_current_agent).description or "")

    )

    print("")



    # Scan for and display instruction files on session start

    _scan_instructions()



    # Load command history for readline navigation

    _load_readline_history(current_session_id)



    # Create display adapter for CLI output

    display_adapter = CLIDisplayAdapter()



    # Collect plugin-registered commands (once at startup)

    plugin_commands = plugin_manager.get_registered_commands()



    # Main conversation loop

    while True:

        # Read user input

        user_input = _read_input()



        # None means end of conversation (EOF on empty input or user chose to exit)

        if user_input is None:

            print("Goodbye!")

            break



        # Empty input — skip

        if not user_input.strip():

            continue



        # Check for slash command

        if user_input.startswith("/"):

            result = _process_slash_command(

                user_input, current_session_id, plugin_commands, display_adapter

            )

            if result == "__exit__":

                print("Goodbye!")

                break

            elif result and result.startswith("__TEMPLATE__:"):

                template_content = result[len("__TEMPLATE__:") :]

                from berserker.provider.base import ChatMessage



                chat_messages = [ChatMessage(role="user", content=template_content)]

                _execute_agent_turn(chat_messages, current_session_id, display_adapter)

            elif result is not None and result != current_session_id:

                current_session_id = result

                _load_readline_history(current_session_id)

                # Update SessionContext when session changes

                _current_ctx = session_manager.switch_to(current_session_id)

            continue



        # Display user message (already shown by input prompt)



        # Save to command history for readline navigation

        try:

            command_history.add(current_session_id, user_input)

            if readline is not None:

                readline.add_history(user_input)  # type: ignore[union-attr]

        except Exception as e:

            logger.warning("Failed to add command to history: %s", e)

            pass

        # Save user message to session via SessionContext (ensures cache invalidation)

        try:

            _current_ctx.append_message("user", user_input)

        except (ValueError, Exception) as exc:

            print("Error saving message: {}".format(exc))

            continue



        # Trigger auto-title generation on first user message

        try:

            from berserker.session.title_gen import trigger_auto_title



            trigger_auto_title(current_session_id)

        except Exception as e:

            logger.warning("Auto-title generation failed: %s", e)

            pass  # Title generation failure should not block conversation

        # Build messages from session history

        messages = _build_messages_from_session(current_session_id)



        # Execute agent turn

        _execute_agent_turn(messages, current_session_id, display_adapter)



        print("")

