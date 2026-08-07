"""

berserker.cli.cli — CLI entry point and command implementations.



Argparse-based CLI with subcommands:

    run     — Non-interactive mode: send a message and get a response

    serve   — Start the server (placeholder)

    models  — List available models (placeholder)

    providers — List configured providers (placeholder)

    session — Session management (placeholder)

    agent   — Agent management (placeholder)

    auth    — Authentication management (placeholder)

    mcp     — MCP server management (placeholder)



Python 3.8.10 compatible: uses type comments, no match/case, no str.removeprefix().

"""



from __future__ import annotations



import argparse

import os

import sys

from typing import Any, Dict, List, Optional



from berserker import __version__



# Agent and permission imports

from berserker.agent.manager import agent_manager

from berserker.permission import permission_checker

from berserker.tool.init import register_default_tools



# Tool registration imports (for _cmd_run parity with interactive mode)

from berserker.tool.registry import registry as tool_registry



# Workspace imports

from berserker.workspace import set_workspace





def _build_parser():

    # type: () -> argparse.ArgumentParser

    """Build and return the argument parser with all subcommands."""

    parser = argparse.ArgumentParser(

        prog="berserker",

        description="The open source AI coding agent.",

    )

    parser.add_argument(

        "-c",

        "--config",

        type=str,

        default=None,

        help="Path to configuration file (default: auto-detect)",

    )

    parser.add_argument(

        "-p",

        "--project",

        type=str,

        default=None,

        help="Project directory (default: current working directory)",

    )

    parser.add_argument(

        "-v",

        "--verbose",

        action="store_true",

        help="Enable verbose/debug output",

    )

    parser.add_argument(

        "--version",

        action="version",

        version="berserker {} (beta)".format(__version__),

    )

    parser.add_argument(

        "--session",

        type=str,

        default=None,

        help="Session ID to continue (interactive mode)",

    )



    subparsers = parser.add_subparsers(dest="command", help="Available commands")



    # ------------------------------------------------------------------

    # run — Non-interactive mode

    # ------------------------------------------------------------------

    run_parser = subparsers.add_parser(

        "run",

        help="Send a message and get a response (non-interactive mode)",

    )

    run_parser.add_argument(

        "message",

        nargs="?",

        default=None,

        help="Message to send (if omitted, reads from stdin)",

    )

    run_parser.add_argument(

        "--provider",

        type=str,

        default=None,

        help="Provider ID to use",

    )

    run_parser.add_argument(

        "--model",

        type=str,

        default=None,

        help="Model ID to use",

    )

    run_parser.add_argument(

        "--session",

        type=str,

        default=None,

        help="Session ID to continue",

    )

    run_parser.add_argument(

        "--config",

        type=str,

        default=None,

        help="Config file override",

    )

    run_parser.add_argument(

        "--verbose",

        action="store_true",

        help="Enable verbose output",

    )

    run_parser.add_argument(

        "--agent",

        type=str,

        default=None,

        help="Agent name to use (default: build)",

    )



    # ------------------------------------------------------------------

    # serve — Start the server

    # ------------------------------------------------------------------

    serve_parser = subparsers.add_parser(

        "serve",

        help="Start the berserker server",

    )

    serve_parser.add_argument(

        "--host",

        type=str,

        default="127.0.0.1",

        help="Host to bind to (default: 127.0.0.1)",

    )

    serve_parser.add_argument(

        "--port",

        type=int,

        default=8080,

        help="Port to bind to (default: 8080)",

    )



    # ------------------------------------------------------------------

    # models — List available models

    # ------------------------------------------------------------------

    subparsers.add_parser(

        "models",

        help="List available models from configured providers",

    )



    # ------------------------------------------------------------------

    # providers — List configured providers

    # ------------------------------------------------------------------

    subparsers.add_parser(

        "providers",

        help="List configured providers",

    )



    # ------------------------------------------------------------------

    # session — Session management

    # ------------------------------------------------------------------

    session_parser = subparsers.add_parser(

        "session",

        help="Session management",

    )

    session_subparsers = session_parser.add_subparsers(

        dest="session_action", help="Session actions"

    )



    session_list_parser = session_subparsers.add_parser(

        "list",

        help="List recent sessions",

    )

    session_list_parser.add_argument(

        "--limit",

        type=int,

        default=10,

        help="Maximum number of sessions to show (default: 10)",

    )



    session_delete_parser = session_subparsers.add_parser(

        "delete",

        help="Delete a session",

    )

    session_delete_parser.add_argument(

        "session_id",

        help="Session ID to delete",

    )



    # ------------------------------------------------------------------

    # agent — Agent management

    # ------------------------------------------------------------------

    subparsers.add_parser(

        "agent",

        help="List configured agents",

    )



    # ------------------------------------------------------------------

    # auth — Authentication management

    # ------------------------------------------------------------------

    subparsers.add_parser(

        "auth",

        help="Show configured API keys (masked)",

    )



    # ------------------------------------------------------------------

    # mcp — MCP server management

    # ------------------------------------------------------------------

    subparsers.add_parser(

        "mcp",

        help="List configured MCP servers",

    )



    return parser





def _read_stdin():

    # type: () -> Optional[str]

    """Read all content from stdin, stripping whitespace.



    Returns:

        The stdin content as a string, or None if empty/not a tty.

    """

    if not sys.stdin.isatty():

        content = sys.stdin.read().strip()

        if content:

            return content

    return None





def _cmd_run(args):

    # type: (argparse.Namespace) -> int

    """Execute the 'run' subcommand.



    Mirrors the interactive mode initialization sequence exactly:

        1. Get message from args or stdin

        2. Load config

        3. Set workspace directory

        4. Load agents and permission overrides from config

        5. Register all tools (same as interactive mode)

        6. Create or load session

        7. Build messages and execute via agent_manager.execute()

        8. Print response to stdout



    Args:

        args: Parsed argparse namespace for the 'run' command.



    Returns:

        Exit code (0 for success, 1 for error).

    """

    # Step 1: Get message

    message = args.message

    if message is None:

        message = _read_stdin()



    if message is None:

        print(

            "Please provide a message or run `python -m berserker` for interactive mode",

            file=sys.stderr,

        )

        return 1



    # Step 2: Load config

    from berserker.config import load_config



    config_path = args.config if args.config else None

    config = load_config(config_path=config_path)  # type: Dict[str, Any]



    # Step 3: Set workspace directory (mirrors interactive mode)

    workspace = args.project if args.project else os.getcwd()

    workspace = os.path.abspath(workspace)

    set_workspace(workspace)



    # Step 4: Load providers from config

    from berserker.provider.base import ProviderError

    from berserker.provider.registry import registry



    registry.load_from_config(config)



    # Step 5: Load agents and permission overrides from config

    agent_manager.load_from_config(config)



    # Step 5.1: Load ticket processing team configs

    import glob as glob_module

    import json

    import tempfile



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

            with open(config_file, "r", encoding="utf-8") as f:

                config_data = json.load(f)



            if primary_model and "agents" in config_data:

                for agent_cfg in config_data["agents"]:

                    if agent_cfg.get("model") == "gpt-4o":

                        # Check if gpt-4o is actually available

                        try:

                            registry.get_provider_for_model("gpt-4o")

                        except Exception:

                            agent_cfg["model"] = primary_model



            # Write patched config to temp file and load

            with tempfile.NamedTemporaryFile(

                mode="w", suffix=".json", delete=False, encoding="utf-8"

            ) as tmp:

                json.dump(config_data, tmp)

                tmp_path = tmp.name

            count = agent_manager.load_from_file(tmp_path)

            os.unlink(tmp_path)

        except Exception as e:

            pass  # Silently skip failed ticket config loads



    permission_checker.load_from_config(config)



    # Step 5.5: Load and activate plugins from config

    from berserker.plugin_system import plugin_manager



    plugin_manager.load_from_config(config)

    plugin_manager.activate_all()



    # Step 6: Register all tools (EXACT same sequence as interactive mode)

    register_default_tools(tool_registry)



    # Step 7: Select provider and model

    provider = None

    model = args.model



    if args.provider:

        try:

            provider = registry.get(args.provider)

        except ProviderError as exc:

            print("Error: {}".format(exc), file=sys.stderr)

            return 1

    elif model:

        try:

            provider, model = registry.get_provider_for_model(model)

        except ProviderError as exc:

            print("Error: {}".format(exc), file=sys.stderr)

            return 1

    else:

        provider_ids = registry.list_providers()

        if not provider_ids:

            print(

                "Error: No providers configured. Please configure at least one provider.",

                file=sys.stderr,

            )

            return 1

        provider = registry.get(provider_ids[0])

        models = provider.list_models()

        if models:

            model = models[0]



    if provider is None:

        print("Error: No provider available.", file=sys.stderr)

        return 1



    if model is None:

        print("Error: No model specified or available.", file=sys.stderr)

        return 1



    # Step 8: Create or load session

    from berserker.session.manager import session_manager



    session_id = args.session

    if session_id:

        session_data = session_manager.load(session_id)

        if session_data is None:

            print(

                "Error: Session '{}' not found.".format(session_id),

                file=sys.stderr,

            )

            return 1

    else:

        session_id = session_manager.create()



    # Step 8.5: Activate session context for message operations

    ctx = session_manager.switch_to(session_id)



    # Step 9: Build messages

    from berserker.provider.base import ChatMessage



    existing_messages = ctx.get_messages()

    messages = []  # type: List[ChatMessage]

    for msg in existing_messages:

        messages.append(

            ChatMessage(

                role=msg.get("role", "user"),

                content=msg.get("content", ""),

                tool_calls=msg.get("tool_calls"),

                tool_call_id=msg.get("tool_call_id") or msg.get("tool_result_for"),

            )

        )



    messages.append(ChatMessage(role="user", content=message))

    ctx.append_message("user", message)



    # Step 10: Execute via agent_manager (enables tool loop + skill injection)

    def _on_tool_call(tool_name, tool_input, result):

        # type: (str, Any, Any) -> None

        """Print tool execution progress to stdout."""

        if args.verbose:

            print("[tool] {} -> {}".format(tool_name, str(result)[:200]), file=sys.stderr)



    try:

        response = agent_manager.execute(

            agent_name=args.agent or "berserker",

            messages=messages,

            session_id=session_id,

            tool_registry=tool_registry,

            on_tool_call=_on_tool_call,

        )

    except ProviderError as exc:

        print("Error: {}".format(exc), file=sys.stderr)

        return 1



    # Step 11: Print response content to stdout

    if response and response.get("content"):

        print(response["content"])



    # Step 12: Append assistant response to session

    if response and response.get("content"):

        ctx.append_message("assistant", response["content"])



    return 0





def _cmd_serve(args):

    # type: (argparse.Namespace) -> int

    """Execute the 'serve' subcommand.



    Starts an HTTP server that exposes the berserker API.

    Currently not implemented - shows helpful message.

    """

    print(

        "Server mode is not yet implemented.\n"

        "Use `python -m berserker` for interactive mode, or\n"

        "`python -m berserker run 'your message'` for non-interactive mode."

    )

    return 1





def _cmd_models(args):

    # type: (argparse.Namespace) -> int

    """Execute the 'models' subcommand.



    Lists all available models from all configured providers.

    """

    from berserker.config import load_config

    from berserker.provider.registry import registry



    config = load_config()  # type: Dict[str, Any]

    registry.load_from_config(config)



    providers = registry.list_providers()

    if not providers:

        print("No providers configured.")

        return 1



    print("Available models:")

    print()

    for provider_id in providers:

        provider = registry.get(provider_id)

        models = provider.list_models()

        if models:

            print("  {} ({}):".format(provider_id, provider.name))

            for model in models:

                print("    - {}".format(model))

        else:

            print("  {} ({}): (no models)".format(provider_id, provider.name))

    return 0





def _cmd_providers(args):

    # type: (argparse.Namespace) -> int

    """Execute the 'providers' subcommand.



    Lists all configured providers with their types and status.

    """

    from berserker.config import load_config

    from berserker.provider.registry import registry



    config = load_config()  # type: Dict[str, Any]

    registry.load_from_config(config)



    providers = registry.list_providers()

    if not providers:

        print("No providers configured.")

        return 1



    print("Configured providers:")

    print()

    for provider_id in providers:

        provider = registry.get(provider_id)

        models = provider.list_models()

        print(

            "  {} (type: {}, models: {})".format(

                provider_id,

                type(provider).__name__,

                len(models),

            )

        )

    return 0





def _cmd_session(args):

    # type: (argparse.Namespace) -> int

    """Execute the 'session' subcommand.



    Manages sessions: list, delete, etc.

    """

    action = getattr(args, "session_action", None)

    if action is None or action == "list":

        return _cmd_session_list(args)

    elif action == "delete":

        return _cmd_session_delete(args)

    else:

        print("Unknown session action: {}".format(action))

        return 1





def _cmd_session_list(args):

    # type: (argparse.Namespace) -> int

    """List recent sessions."""

    from berserker.session.manager import session_manager



    limit = getattr(args, "limit", 10)

    sessions = session_manager.list_sessions(limit=limit)



    if not sessions:

        print("No sessions found.")

        return 0



    print("Recent sessions:")

    print()

    print("  {:<40} {:<32} {}".format("ID", "Title", "Created"))

    print("  " + "-" * 80)

    for session in sessions:

        import datetime



        created = datetime.datetime.fromtimestamp(session["created_at"]).strftime("%Y-%m-%d %H:%M")

        title = session.get("title", "") or ""

        if not title:

            display_title = "(untitled)"

        elif len(title) > 30:

            display_title = title[:30] + "..."

        else:

            display_title = title

        print(

            "  {:<40} {:<32} {}".format(

                session["id"],

                display_title,

                created,

            )

        )

    return 0





def _cmd_session_delete(args):

    # type: (argparse.Namespace) -> int

    """Delete a session."""

    from berserker.session.manager import session_manager



    session_id = args.session_id

    success = session_manager.delete(session_id)

    if success:

        print("Session '{}' deleted.".format(session_id))

        return 0

    else:

        print("Session '{}' not found.".format(session_id))

        return 1





def _cmd_agent(args):

    # type: (argparse.Namespace) -> int

    """Execute the 'agent' subcommand.



    Lists all configured agents with their models and permissions.

    """

    from berserker.agent.manager import agent_manager

    from berserker.config import load_config



    config = load_config()  # type: Dict[str, Any]

    agent_manager.load_from_config(config)



    agents = agent_manager.list()

    if not agents:

        print("No agents configured.")

        return 1



    print("Configured agents:")

    print()

    print("  {:<15} {:<30} {:<12} {}".format("Name", "Model", "Mode", "Permission"))

    print("  " + "-" * 72)

    for agent in agents:

        print(

            "  {:<15} {:<30} {:<12} {}".format(

                agent.name,

                agent.model,

                agent.mode,

                agent.permission,

            )

        )

    return 0





def _cmd_auth(args):

    # type: (argparse.Namespace) -> int

    """Execute the 'auth' subcommand.



    Shows configured API keys (masked) for all providers.

    """

    from berserker.config import load_config



    config = load_config()  # type: Dict[str, Any]

    providers = config.get("providers", {})  # type: Dict[str, Any]



    if not providers:

        print("No providers configured.")

        return 1



    print("Configured authentication:")

    print()

    for provider_id, provider_config in providers.items():

        provider_config = provider_config  # type: Dict[str, Any]

        api_key = provider_config.get("api_key", "")

        if api_key:

            # Mask the API key, showing first 4 and last 4 characters

            if len(api_key) > 8:

                masked = api_key[:4] + "..." + api_key[-4:]

            else:

                masked = "****"

        else:

            masked = "(not set)"

        print("  {}: {}".format(provider_id, masked))

    return 0





def _cmd_mcp(args):

    # type: (argparse.Namespace) -> int

    """Execute the 'mcp' subcommand.



    Lists configured MCP servers from config.

    """

    from berserker.config import load_config



    config = load_config()  # type: Dict[str, Any]

    mcp_config = config.get("mcp", {})  # type: Dict[str, Any]

    servers = mcp_config.get("servers", {})  # type: Dict[str, Any]



    if not servers:

        print("No MCP servers configured.")

        return 0



    print("Configured MCP servers:")

    print()

    for server_name, server_config in servers.items():

        transport = server_config.get("transport", "unknown")

        if transport == "stdio":

            command = server_config.get("command", "")

            cmd_args = server_config.get("args", [])

            print(

                "  {} (stdio): {} {}".format(

                    server_name,

                    command,

                    " ".join(cmd_args),

                )

            )

        elif transport == "http":

            url = server_config.get("url", "")

            print("  {} (http): {}".format(server_name, url))

        else:

            print("  {} ({})".format(server_name, transport))

    return 0





# Map command names to handler functions

_COMMAND_HANDLERS = {

    "run": _cmd_run,

    "serve": _cmd_serve,

    "models": _cmd_models,

    "providers": _cmd_providers,

    "session": _cmd_session,

    "agent": _cmd_agent,

    "auth": _cmd_auth,

    "mcp": _cmd_mcp,

}  # type: dict





def main():

    # type: () -> None

    """Main entry point for the berserker CLI.



    Parses command-line arguments and dispatches to the appropriate

    subcommand handler.

    """

    parser = _build_parser()

    args = parser.parse_args()



    if args.command is None:

        # No subcommand given — start interactive conversation mode

        from berserker.cli.conversation import start_conversation



        # Determine workspace directory

        workspace = args.project if args.project else os.getcwd()

        workspace = os.path.abspath(workspace)



        # Load config if path specified

        config = None  # type: Optional[Dict[str, Any]]

        if args.config:

            from berserker.config import load_config



            config = load_config(config_path=args.config, cwd=workspace)



        start_conversation(config=config, session_id=args.session, workspace=workspace)

        sys.exit(0)



    handler = _COMMAND_HANDLERS.get(args.command)

    if handler is None:

        print("Unknown command: {}".format(args.command), file=sys.stderr)

        parser.print_help()

        sys.exit(1)



    exit_code = handler(args)

    sys.exit(exit_code)





if __name__ == "__main__":

    main()

