"""

berserker.gui.app — GUI application entry point.



Integrates the wxPython GUI with berserker's agent execution,

session management, and tool registration.



Python 3.8.10 compatible: uses type comments, no | union syntax.

"""



from __future__ import annotations



import logging

import os

from typing import Any, Dict, List, Optional



import wx



logger = logging.getLogger(__name__)



from berserker import __version__

from berserker.agent.manager import agent_manager

from berserker.gui.controller import GUIController

from berserker.gui.main import PyBerserkerFrame

from berserker.gui.sidebar import SidebarPanel

from berserker.provider.base import ChatMessage

from berserker.provider.registry import registry as provider_registry

from berserker.session.context import SessionContext

from berserker.session.manager import session_manager

from berserker.session.message_utils import chat_messages_from_session

from berserker.tool.init import register_default_tools

from berserker.tool.registry import registry as tool_registry

from berserker.workspace.manager import WorkspaceManager



logger = logging.getLogger(__name__)



# Global controller instance

_controller = None  # type: Optional[GUIController]

_current_agent = "berserker"  # type: str

_current_model = None  # type: Optional[str]

_current_session_id = None  # type: Optional[str]

_session_context = None  # type: Optional[SessionContext]





def _reset_global_session_state():

    # type: () -> None

    """Reset global session state when workspace changes.



    Called by PyBerserkerFrame.set_workspace() to clear stale session

    references that belong to the old workspace.

    """

    global _current_session_id, _session_context

    _current_session_id = None

    _session_context = None

    logger.info("[APP] Global session state reset for workspace switch")





def start_gui_app(config=None, session_id=None, workspace=None):

    # type: (Optional[Dict[str, Any]], Optional[str], Optional[str]) -> None

    """Start the berserker GUI application.



    Args:

        config: Configuration dictionary. If None, loaded from default config files.

        session_id: Optional session ID to start with.

        workspace: Workspace directory. If None, defaults to current directory.

    """

    global _controller, _current_session_id, _current_model, _session_context



    # Set workspace

    from berserker.workspace import set_workspace



    if workspace:

        set_workspace(workspace)



    # Load config if not provided

    if config is None:

        from berserker.config import load_config

        from berserker.workspace import get_workspace



        config = load_config(config_path=None, cwd=get_workspace())



    # Initialize logging

    logging_config = config.get("logging", {})  # type: Dict[str, Any]

    log_enabled = logging_config.get("enabled", True)

    log_file = logging_config.get("file")

    log_level = logging_config.get("level")



    file_level = None  # type: Optional[int]

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



    from berserker.logging_config import setup_logging



    setup_logging(log_file=log_file, file_level=file_level, enabled=log_enabled)



    # Load providers from config

    provider_registry.load_from_config(config)



    # Load agent overrides from config

    agent_manager.load_from_config(config)



    # Load ticket processing team configs

    import glob as glob_module

    import json

    import os



    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    ticket_configs = glob_module.glob(os.path.join(base_dir, "agent", "configs", "ticket-*.json"))



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

            pass  # Silently skip failed ticket config loads



    # Log agent state after config load (compact)

    try:

        build_agent = agent_manager.get("berserker")

        logger.info(

            "[AGENT] After load_from_config: name=%s, mode=%s, model=%s, permission=%s, tools=%d, prompt_start=%s",

            build_agent.name,

            build_agent.mode,

            build_agent.model,

            build_agent.permission,

            len(build_agent.tools),

            repr(build_agent.system_prompt[:80]),

        )

    except KeyError as e:

        logger.error("[AGENT] Berserker agent NOT FOUND after load_from_config: %s", e)



    # Load permission rules from config

    from berserker.permission import permission_checker



    permission_checker.load_from_config(config)



    # Load and activate plugins from config

    from berserker.plugin_system import plugin_manager



    plugin_manager.load_from_config(config)

    plugin_manager.activate_all()



    # Register all tools

    register_default_tools(tool_registry)



    # Scan all command sources (builtin + user + project + plugin)

    from berserker.command.registry import command_registry



    command_registry.scan(workspace if workspace else os.getcwd())



    # Get workspace ID for sidebar integration

    workspace_dir = workspace if workspace else os.getcwd()

    workspace_mgr = WorkspaceManager()

    workspace_id = workspace_mgr.get_or_create(workspace_dir)



    # No auto-selection: require explicit user session selection

    _current_session_id = None

    _session_context = None



    # Create wx application

    app = wx.App()



    # Create frame with callbacks

    frame = PyBerserkerFrame(

        title="berserker {} — No Session Selected".format(__version__),

        on_send=_on_send_message,

        on_stop=_on_stop_execution,

        show_welcome=False,  # app.py shows its own welcome

    )



    # Disable input until a session is selected

    frame.set_send_enabled(False)



    # Create controller with session and agent state

    _controller = GUIController(

        frame,

        session_id=_current_session_id,

        current_agent=_current_agent,

        workspace_id=workspace_id,

    )

    logger.info("[AGENT] Controller initialized with current_agent=%s", _controller.current_agent)

    _controller.set_session_context(_session_context)



    # Link controller to frame for dropdown callbacks

    frame.controller = _controller



    # Register workspace change callback to reset global session state

    frame.set_workspace_change_callback(_reset_global_session_state)



    # Populate ModelAgentBar dropdowns

    # Get primary agents only (build, plan)

    primary_agents = agent_manager.list_primary()

    agent_names = [a.name for a in primary_agents]

    frame.model_agent_bar.set_agents(agent_names)

    frame.model_agent_bar.set_selected_agent(_current_agent)

    logger.info("[AGENT] Dropdown agents=%s, selected=%s", agent_names, _current_agent)



    # Get available models from provider registry

    all_models = []

    for provider_id, models in provider_registry.list_all_models().items():

        for model in models:

            all_models.append("{}/{}".format(provider_id, model))

    if all_models:

        frame.model_agent_bar.set_models(all_models)

        # Set default model based on current agent's config

        if _current_model:

            frame.model_agent_bar.set_selected_model(_current_model)

        else:

            # Use the model configured for the current agent

            try:

                current_agent_info = agent_manager.get(_current_agent)

                frame.model_agent_bar.set_selected_model(current_agent_info.model)

                _current_model = current_agent_info.model

            except KeyError:

                pass



    # Create and attach sidebar panel

    sidebar_panel = SidebarPanel(

        frame.main_panel,

        session_manager=session_manager,

        workspace_manager=workspace_mgr,

    )

    frame.set_sidebar(sidebar_panel)



    # Setup sidebar callbacks on controller

    _controller.setup_sidebar(sidebar_panel)



    # Update session manager's project_id FIRST so sidebar refresh uses correct project_id

    session_manager.update_project_id(workspace_dir)



    # Initialize sidebar with current workspace

    sidebar_panel.set_workspace(workspace_id)



    # Sync workspace to frame (updates status bar and title) - must be after sidebar is attached

    if workspace:

        frame.set_workspace(workspace)



    # Show frame

    frame.Show()



    # Run main loop

    app.MainLoop()





def _on_send_message(text):

    # type: (str) -> None

    """Handle user sending a message."""

    global _current_agent, _session_context



    if _controller is None or _controller.session_id is None:

        return



    # Ensure session context matches the controller's current session ID.

    # This handles session switches, deletions, and new session creation

    # where _session_context may point to a stale/deleted session.

    if _session_context is None or _controller.session_id != _session_context.session_id:

        _session_context = session_manager.switch_to(_controller.session_id)



    # Use controller's session_id which is updated during session switching

    active_session_id = _controller.session_id



    # Check for slash commands first

    is_command, result = _controller.process_input(text)

    # Passthrough case: a leading "/<name>" that is NOT a registered command
    # (e.g. "/skill-creator <user message>"). process() returned
    # (False, stripped_text) — use the de-slashed text as the real user
    # message so the LLM decides whether to invoke the matching SkillAsTool.
    if not is_command and result:
        text = result

    if is_command:

        # Handle special __INIT__ return (triggers AGENTS.md generation)

        if result.startswith("__INIT__"):

            # Convert to agent instruction for AGENTS.md generation

            focus = result.replace("__INIT__", "").lstrip(":")

            init_instruction = _build_init_instruction(focus)

            # Display as user message

            _controller.display_user_message(text)

            # Save to session

            try:

                _session_context.append_message("user", text)

            except Exception as exc:

                logger.error("Failed to save message: {}".format(exc))

                _controller.display_error("Failed to save message: {}".format(exc))

                return

            # Build messages and execute agent with init instruction

            raw_messages = _session_context.build_messages_for_llm()

            messages = chat_messages_from_session(raw_messages)

            # Append the init instruction as a system-level hint

            messages.append(ChatMessage(role="user", content=init_instruction))

            _controller.execute_agent(

                agent_manager,

                _controller.current_agent,

                messages,

                active_session_id,

                tool_registry,

            )

        elif result and result.startswith("__TEMPLATE__:"):

            # Template command → execute as agent instruction

            template_content = result[len("__TEMPLATE__:") :]

            messages = [ChatMessage(role="user", content=template_content)]

            _controller.execute_agent(

                agent_manager,

                _controller.current_agent,

                messages,

                _controller.session_id,

                tool_registry,

            )

        elif result:

            # Display command result (e.g., /help, /agent list)

            _controller.display_assistant_message(result)

        # Commands with empty result (e.g., /clear) already handle their own display

        return



    # Not a command — display user message and execute agent

    if _controller:

        logger.info("[AGENT] Sending message with agent=%s", _controller.current_agent)

        try:

            active_agent = agent_manager.get(_controller.current_agent)

            has_readonly = (

                "READ-ONLY" in active_agent.system_prompt

                or "read-only" in active_agent.system_prompt.lower()

            )

            logger.info(

                "[AGENT] Active agent: mode=%s, permission=%s, has_readonly=%s",

                active_agent.mode,

                active_agent.permission,

                has_readonly,

            )

        except KeyError as e:

            logger.error("[AGENT] Active agent NOT FOUND: %s", e)



    _controller.display_user_message(text)



    # Save user message to session

    try:

        _session_context.append_message("user", text)

    except Exception as exc:

        logger.error("Failed to save message: {}".format(exc))

        _controller.display_error("Failed to save message: {}".format(exc))

        return



    # Trigger auto-title generation on first user message

    try:

        from berserker.session.title_gen import trigger_auto_title



        trigger_auto_title(_controller.session_id or "")

    except Exception as e:

        logger.warning("Auto-title generation failed: %s", e)

        pass  # Title generation failure should not block conversation



    # Build messages from session history

    raw_messages = _session_context.build_messages_for_llm()

    messages = chat_messages_from_session(raw_messages)



    # Execute agent

    _controller.execute_agent(

        agent_manager,

        _controller.current_agent,

        messages,

        active_session_id,

        tool_registry,

    )





def _build_init_instruction(focus):

    # type: (str) -> str

    """Build AGENTS.md generation instruction using shared prompt template."""

    from berserker.agent.init_prompt import build_init_prompt, load_existing_agents_md

    from berserker.workspace import get_workspace



    workspace = get_workspace()

    existing_agents_md = load_existing_agents_md(workspace)

    agents_md_path = os.path.join(workspace, "AGENTS.md")



    return build_init_prompt(

        user_focus=focus,

        analysis_text=None,  # GUI: no pre-scan, let LLM explore autonomously

        existing_agents_md=existing_agents_md,

        agents_md_path=agents_md_path,

    )





def _on_stop_execution():

    # type: () -> None

    """Handle user clicking stop button."""

    if _controller:

        _controller.abort_execution()

