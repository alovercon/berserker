"""Agent execution logic extracted from AgentManager."""

import copy
import hashlib
import json
import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

from berserker.agent.compaction import CompactionStrategy
from berserker.agent.messaging import message_router
from berserker.bus import (
    bus,
    COMPACTION_STARTED,
    COMPACTION_COMPLETED,
    SNAPSHOT_CREATED,
    PRUNING_COMPLETED,
    CompactionStartedData,
    CompactionCompletedData,
    PruningCompletedData,
    SnapshotCreatedData,
)
from berserker.permission import ALLOWED, DENIED, NEEDS_ASK, permission_checker
from berserker.provider.base import (
    ChatMessage,
    ChatResponse,
    ContextLengthExceeded,
    FINISH_CONTENT_FILTER,
    FINISH_ERROR,
    FINISH_LENGTH,
    FINISH_REFUSAL,
    FINISH_STOP,
    FINISH_TOOL_CALLS,
    VALID_FINISH_REASONS,
    ModelNotFoundError,
)
from berserker.provider.registry import registry as provider_registry
from berserker.session.instruction import instruction_loader
from berserker.session.manager import session_manager
from berserker.session.snapshot import SnapshotTracker
from berserker.session.token_counter import TokenCounter
from berserker.storage import get_db, insert
from berserker.tool.base import ToolContext
from berserker.tool.registry import ToolRegistry
from berserker.tool.todo import _todo_store
from berserker.tool.truncate import count_tokens
from berserker.workspace import get_workspace

from berserker.agent.constants import (
    _chat_message_to_dict,
    _DEFAULT_CONTEXT_LIMIT,
    _COMPACTION_BUFFER,
    _make_max_steps_prompt,
)

from berserker.agent.monitor import agent_monitor, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED

logger = logging.getLogger(__name__)

# Markup fragments models emit when they write tool calls as plain text
# instead of using native tool_calls (DeepSeek DSML, XML-ish invoke blocks,
# and similar pseudo formats). Detection is deliberately conservative -
# these strings essentially never appear in legitimate prose answers.
_TEXT_TOOLCALL_MARKERS = (
    "DSML",
    "<｜",
    "<invoke",
    "</invoke>",
    "<tool_calls>",
    "</tool_calls>",
    "<function_calls>",
    "<|tool_call",
    "<tool_call",
)


def _contains_text_toolcall(content):
    # type: (str) -> bool
    """True when the assistant content carries tool-call markup as text."""
    if not content:
        return False
    head = content[:8000]  # such markup always appears near the top
    return any(m in head for m in _TEXT_TOOLCALL_MARKERS)


class TextModeToolCallError(RuntimeError):
    """The model repeatedly emitted tool calls as text instead of native
    tool_calls - the turn cannot be trusted as a completed answer."""


class AgentExecutor(object):
    """Handles agent execution: provider calls, tool loops, compaction, persistence."""

    def __init__(self, manager):
        # type: (Any) -> None
        self._manager = manager

    def execute(
        self,
        agent_name,  # type: str
        messages,  # type: List[ChatMessage]
        session_id,  # type: str
        tool_registry,  # type: ToolRegistry
        on_tool_call=None,  # type: Optional[Callable[[str, Dict[str, Any], Dict[str, Any]], None]]
        on_permission_ask=None,  # type: Optional[Callable[[str, Dict[str, Any]], bool]]
        extra=None,  # type: Optional[Dict[str, Any]]
        abort_event=None,  # type: Optional[threading.Event]
    ):
        # type: (...) -> Dict[str, Any]
        """Run a single agent turn with tool call loop support.

        Executes the specified agent against the given message history:
        1. Get agent configuration
        2. Get provider for the agent's model
        3. Filter tools to agent's allowed list
        4. Call provider.chat() with system prompt + messages + tools
        5. If response indicates tool calls, execute them via tool_registry
        6. Append tool results as tool messages and loop (max iterations)
        7. Persist the assistant response via session_manager
        8. Return dict with 'content' and 'usage' keys

        Args:
            agent_name: Name of the agent to execute.
            messages: List of ChatMessage objects forming the conversation.
            session_id: Session ID for message persistence.
            tool_registry: ToolRegistry for executing tool calls.
            on_tool_call: Optional callback(tool_name, args, result) called after
                          each tool execution for progress display.
            on_permission_ask: Optional callback(tool_name, args) -> bool called when
                               a tool needs user permission (ask mode). Returns True to allow,
                               False to deny. If None, ask-mode tools are auto-allowed.
            extra: Optional dict of extra context passed to tools via ToolContext.extra.
                   Useful for GUI callbacks, custom state, etc.
            abort_event: Optional threading.Event to signal abort.

        Returns:
            Dict with keys:
                - 'content': The assistant's text response (str).
                - 'usage': Token usage dict with keys: prompt_tokens, completion_tokens, total_tokens.
                - 'finish_reason': The finish reason from the provider.

        Raises:
            KeyError: If the agent is not found.
            ModelNotFoundError: If the agent's model has no provider.
        """
        # Step 1: Get agent configuration
        agent = self._manager.get(agent_name)

        # Step 1.1: Register with agent monitor
        agent_id = "{}-{}".format(agent_name, session_id)
        agent_monitor.register_agent(agent_id, agent_name, task="Initializing...")
        agent_monitor.update_status(agent_id, STATUS_RUNNING)
        agent_monitor.update_phase(agent_id, "init", "Initializing...")

        # Log agent state at executor entry (compact)
        has_readonly = 'READ-ONLY' in agent.system_prompt or 'read-only' in agent.system_prompt.lower()
        logger.info(
            "[EXECUTOR] agent_name=%s, mode=%s, model=%s, permission=%s, tools=%d, has_readonly=%s, prompt_start=%s",
            agent.name, agent.mode, agent.model, agent.permission, len(agent.tools),
            has_readonly, repr(agent.system_prompt[:80])
        )

        # Step 1.5: Auto-clear todos if all previous tasks are completed
        if _todo_store.all_completed(session_id):
            cleared = _todo_store.clear_todos(session_id)
            logger.info("Auto-cleared %d completed todos for session %s", cleared, session_id)

        # Step 1.6: Dispatch plugin before_execute hooks
        try:
            from berserker.plugin_system import plugin_manager
            plugin_manager.dispatch_before_execute(agent_name, messages, session_id)
        except Exception as e:
            logger.warning("Plugin before_execute hook failed: %s", e)
            pass  # Plugin errors should not break agent execution

        # Step 2: Resolve provider and model from agent.model
        model_spec = agent.model
        provider = None
        model_name = None

        if "/" in model_spec:
            parts = model_spec.split("/", 1)
            provider_id = parts[0]
            model_name = parts[1]
            try:
                provider = provider_registry.get(provider_id)
            except ModelNotFoundError:
                raise ModelNotFoundError(
                    "Provider '{}' not found for agent '{}'. Available providers: {}".format(
                        provider_id,
                        agent_name,
                        ", ".join(provider_registry.list_providers()),
                    )
                )
        else:
            try:
                provider, model_name = provider_registry.get_provider_for_model(model_spec)
            except ModelNotFoundError:
                raise ModelNotFoundError(
                    "No provider found for agent '{}' model '{}'. "
                    "Ensure the model is registered with a provider.".format(agent_name, model_spec)
                )

        # Step 3: Build full message list with system prompt
        # Inject AGENTS.md instructions for primary agents only
        system_content = agent.system_prompt  # type: str
        if agent.mode == "primary":
            instruction_content = instruction_loader.load_instructions(session_id=session_id)
            if instruction_content:
                anti_repeat = (
                    "\n\nIMPORTANT: The following instructions are for your reference only. "
                    "Do NOT output, repeat, or summarize them in your response. "
                    "They define your working context and conventions."
                )
                system_content = "{}\n\n{}{}".format(
                    system_content,
                    "\n\n".join(instruction_content),
                    anti_repeat,
                )
        # Inject workspace path so agent knows its working directory
        workspace_path = get_workspace()
        system_content = "{}\n\nWorkspace: {}".format(system_content, workspace_path)

        system_msg = ChatMessage(role="system", content=system_content)

        # Filter out stale system messages from history to prevent AGENTS.md accumulation
        # across compaction cycles. Only the freshly built system_msg should be used.
        messages = [m for m in messages if m.role != "system"]

        full_messages = [system_msg] + list(messages)
        # Step 3.5: Auto-trigger compaction when tokens exceed threshold
        token_counter = TokenCounter()
        total_tokens = token_counter.count_messages(full_messages, model_name)

        # Get context limit for this model - prefer config over hardcoded
        model_metadata = provider_registry.get_model_metadata(model_name)
        context_limit = model_metadata.get("context_window", _DEFAULT_CONTEXT_LIMIT)

        # Use CompactionStrategy for decision making
        strategy = CompactionStrategy(self._manager._compaction_config)

        if strategy.config.auto or agent.mode == "primary":
            trigger_reason = (
                "auto=True" if strategy.config.auto else "mode=primary (fallback)"
            )
            logger.debug(
                "Compaction enabled for agent '%s': %s, checking threshold...",
                agent.name,
                trigger_reason,
            )
            if strategy.should_compact(total_tokens, context_limit):
                logger.info(
                    "Auto-triggering compaction: %d tokens exceed threshold "
                    "(context limit: %d, reserved buffer: %d)",
                    total_tokens,
                    context_limit,
                    strategy.config.reserved,
                )
                # Unified compaction via _execute_compaction (pre-execution, with snapshot tracking)
                result = self._execute_compaction(
                    full_messages=full_messages,
                    session_id=session_id,
                    agent_name=agent.name,
                    model_name=model_name,
                    context_limit=context_limit,
                    strategy=strategy,
                    token_counter=token_counter,
                    tracker=SnapshotTracker(),
                    reason="token_overflow",
                    do_fallback_truncate=True,
                    system_msg=system_msg,
                )
                full_messages = result["full_messages"]
            else:
                logger.debug(
                    "Compaction skipped for agent '%s': %d tokens below threshold "
                    "(context limit: %d, reserved buffer: %d)",
                    agent.name,
                    total_tokens,
                    context_limit,
                    strategy.config.reserved,
                )
        else:
            logger.debug(
                "Compaction disabled for agent '%s': auto=False and mode='%s' (not primary)",
                agent.name,
                agent.mode,
            )

        # Step 4: Filter and convert tools for this agent
        allowed_tool_ids = agent.tools  # type: List[str]
        # Handle None tool_registry (e.g., title agent doesn't need tools)
        if tool_registry is None:
            filtered_tools = []  # type: List[Any]
            tool_schemas = []  # type: List[Dict[str, Any]]
        else:
            all_tools = tool_registry.list_all()
            # With SkillAsTool architecture, skills are registered as individual tools
            # (ID = skill name like "curl", "pua", etc.). If agent has "skill" permission,
            # include all SkillAsTool instances.
            has_skill_permission = "skill" in allowed_tool_ids
            filtered_tools = []  # type: List[Any]
            for tool in all_tools:
                if tool.id in allowed_tool_ids:
                    filtered_tools.append(tool)
                elif has_skill_permission:
                    # Check if this is a SkillAsTool instance
                    try:
                        from berserker.tool.skill import SkillAsTool

                        if isinstance(tool, SkillAsTool):
                            filtered_tools.append(tool)
                    except Exception as e:
                        logger.warning("SkillAsTool check failed during tool filtering: %s", e)
                        pass
            tool_schemas = provider.map_tools(filtered_tools)  # type: List[Dict[str, Any]]

        # Step 5: Tool call loop (max iterations from agent config)
        max_tool_iterations = agent.max_tool_iterations
        iteration = 0
        response = None  # type: Optional[ChatResponse]
        context_retry_count = 0  # type: int

        # Track consecutive empty-response tool-call loops to detect stuck agents
        _empty_tool_loop_count = 0  # type: int
        _max_empty_tool_loops = 20  # type: int
        _stuck_aborted = False  # type: bool  # set when the stuck-loop guard forces a stop

        # Execution stats surfaced to callers (task tool annotations)
        _tool_calls_executed = 0  # type: int
        _tools_used = {}  # type: Dict[str, int]

        # Text-mode tool-call retries (model wrote tool markup as content)
        _text_toolcall_count = 0  # type: int
        _max_text_toolcall_retries = 1  # type: int

        # Cache per-content token counts so the per-iteration monitor update
        # and the per-tool-call context_info calculation do not re-encode the
        # full history on every pass.
        _token_count_cache = {}  # type: Dict[Any, int]

        def _cached_count_tokens(text):
            if text not in _token_count_cache:
                _token_count_cache[text] = count_tokens(text)
            return _token_count_cache[text]

        # Mark agent as thinking (about to make first LLM call)
        agent_monitor.update_phase(agent_id, "thinking", "Calling LLM...")

        while iteration < max_tool_iterations:            # Check abort signal at start of each iteration
            if abort_event is not None and abort_event.is_set():
                logger.info("Execution aborted at iteration %d (before provider call)", iteration)
                agent_id = "{}-{}".format(agent_name, session_id)
                agent_monitor.update_status(agent_id, STATUS_FAILED)
                return {"content": "", "usage": None, "finish_reason": "abort"}
            iteration += 1

            # Update monitor with current iteration and token count.
            # Sum per-message counts through the content cache so repeated
            # iterations only re-encode newly appended messages instead of
            # the full history (count_messages is additive over messages).
            token_counter = TokenCounter()
            current_tokens = 0
            for _msg in full_messages:
                _msg_key = (
                    _msg.role,
                    _msg.content or "",
                    repr(getattr(_msg, "tool_calls", None)),
                    getattr(_msg, "reasoning_content", None) or "",
                )
                if _msg_key not in _token_count_cache:
                    _token_count_cache[_msg_key] = token_counter.count_messages(
                        [_msg], model_name
                    )
                current_tokens += _token_count_cache[_msg_key]
            agent_monitor.update_iteration(agent_id, iteration, current_tokens)

            # Check token usage and trigger compaction if needed (primary agents only)
            if agent.mode == "primary":
                model_metadata = provider_registry.get_model_metadata(model_name)
                context_limit = model_metadata.get("context_window", 128000)

                strategy = CompactionStrategy(self._manager._compaction_config)
                if strategy.should_compact(current_tokens, context_limit):
                    logger.info(
                        "Loop compaction triggered at iteration %d: %d tokens exceed threshold "
                        "(context limit: %d, reserved buffer: %d)",
                        iteration,
                        current_tokens,
                        context_limit,
                        strategy.config.reserved,
                    )

                    # Unified compaction via _execute_compaction (loop compaction, with snapshot tracking)
                    agent_monitor.update_phase(agent_id, "compacting", "Compacting messages (loop)...")
                    result = self._execute_compaction(
                        full_messages=full_messages,
                        session_id=session_id,
                        agent_name=agent.name,
                        model_name=model_name,
                        context_limit=context_limit,
                        strategy=strategy,
                        token_counter=token_counter,
                        tracker=SnapshotTracker(),
                        reason="loop_token_overflow",
                        do_fallback_truncate=False,
                        system_msg=system_msg,
                    )
                    full_messages = result["full_messages"]
                    agent_monitor.update_phase(agent_id, "thinking", "Calling LLM...")
            # Call provider with tools
            chat_options = {}  # type: Dict[str, Any]
            if tool_schemas:
                chat_options["tools"] = tool_schemas

            # Defensive validation: ensure first message is system role
            if full_messages and full_messages[0].role != "system":
                logger.warning(
                    "First message is not system role (%s), reordering messages",
                    full_messages[0].role,
                )
                system_msgs = [m for m in full_messages if m.role == "system"]
                non_system_msgs = [m for m in full_messages if m.role != "system"]
                full_messages = system_msgs + non_system_msgs

            try:
                response = provider.chat(full_messages, model_name, **chat_options)
            except ContextLengthExceeded:
                if context_retry_count >= 2:
                    # Already retried twice, re-raise to let normal error handling deal with it
                    raise
                logger.warning(
                    "Context length exceeded on iteration %d, compacting messages and retrying once...",
                    iteration,
                )

                # Create a copy to avoid mutating the original conversation history
                compacted_messages = copy.deepcopy(full_messages)

                # Apply pruning first (strip old tool outputs)
                compacted_messages = self._manager.prune_messages(
                    compacted_messages, session_id=session_id
                )

                # Then compact using the existing compaction logic
                model_metadata = provider_registry.get_model_metadata(model_name)
                context_limit = model_metadata.get("context_window", 128000)
                strategy = CompactionStrategy(self._manager._compaction_config)
                target_tokens = context_limit - strategy.config.reserved

                compacted_messages = self._manager.compact(compacted_messages, max_tokens=target_tokens)

                logger.info(
                    "Messages compacted for retry: %d -> %d messages",
                    len(full_messages),
                    len(compacted_messages),
                )

                # Increment retry counter
                context_retry_count += 1
                # Retry the provider call with compacted messages
                response = provider.chat(compacted_messages, model_name, **chat_options)

                # Update full_messages to the compacted version for subsequent iterations
                full_messages = compacted_messages

                # Unified compaction via _execute_compaction (retry compaction, NO snapshot tracking)
                # Note: We pass full_messages (already compacted) to persist them
                agent_monitor.update_phase(agent_id, "compacting", "Compacting messages (context retry)...")
                result = self._execute_compaction(
                    full_messages=full_messages,
                    session_id=session_id,
                    agent_name=agent.name,
                    model_name=model_name,
                    context_limit=context_limit,
                    strategy=strategy,
                    token_counter=token_counter,
                    tracker=None,  # No snapshot tracking for retry compaction
                    reason="context_retry",
                    do_fallback_truncate=False,
                    system_msg=system_msg,
                )
                full_messages = result["full_messages"]
                agent_monitor.update_phase(agent_id, "thinking", "Calling LLM (retry)...")
            # Check abort signal after provider returns (cannot interrupt blocking HTTP call)
            if abort_event is not None and abort_event.is_set():
                logger.info("Execution aborted at iteration %d (after provider call)", iteration)
                agent_id = "{}-{}".format(agent_name, session_id)
                agent_monitor.update_status(agent_id, STATUS_FAILED)
                return {"content": "", "usage": None, "finish_reason": "abort"}
            # Log LLM response for debugging
            content_preview = (response.content or "")[:200]
            has_tools = bool(response.tool_calls)
            logger.info(
                "LLM response (iteration %d): finish_reason=%s, has_tools=%s, content_len=%d, preview=%s",
                iteration,
                response.finish_reason,
                has_tools,
                len(response.content or ""),
                repr(content_preview),
            )

            # Detect stuck-in-tool-loop: empty text + tool calls repeatedly
            if has_tools and not (response.content or "").strip():
                _empty_tool_loop_count += 1
                logger.warning(
                    "[STUCK_DETECT] Iteration %d: empty content with tool calls (%d consecutive). "
                    "Agent=%s, tool_count=%d",
                    iteration, _empty_tool_loop_count, agent_name,
                    len(response.tool_calls) if response.tool_calls else 0,
                )
                if _empty_tool_loop_count >= _max_empty_tool_loops:
                    logger.error(
                        "[STUCK_ABORT] Agent %s stuck in empty-tool loop for %d iterations. "
                        "Forcing stop.",
                        agent_name, _empty_tool_loop_count,
                    )
                    _stuck_aborted = True
                    break
            else:
                # Reset counter on any non-empty response
                _empty_tool_loop_count = 0

            if has_tools and response.tool_calls is not None:
                for tc in response.tool_calls:
                    logger.info(
                        "  Tool call: %s(%s)",
                        tc.get("function", {}).get("name", ""),
                        tc.get("function", {}).get("arguments", "")[:200],
                    )

            # Text-mode tool-call detection: the model wrote tool-call markup
            # (DSML, <invoke ...>, ...) as plain content instead of native
            # tool_calls. Never accept that as a final answer - the turn ends
            # silently with zero work done and invites hallucinated follow-ups.
            # Retry once with a corrective nudge; fail explicitly otherwise.
            if not response.tool_calls and _contains_text_toolcall(response.content):
                _text_toolcall_count += 1
                if _text_toolcall_count <= _max_text_toolcall_retries:
                    logger.warning(
                        "[TEXT_TOOLCALL] Agent %s emitted tool-call markup as plain text "
                        "(iteration %d) - retrying with corrective nudge. preview=%s",
                        agent_name, iteration, repr((response.content or "")[:200]),
                    )
                    full_messages.append(
                        ChatMessage(
                            role="assistant",
                            content=response.content or "",
                            reasoning_content=getattr(response, "reasoning_content", None),
                        )
                    )
                    full_messages.append(
                        ChatMessage(
                            role="user",
                            content=(
                                "你的上一条回复在正文中输出了工具调用标记文本，但没有发起真正的工具调用。"
                                "请直接通过原生 tool_calls 使用系统提供的工具完成操作；"
                                "不要在正文或代码块中书写任何工具调用标记（如 DSML、<invoke> 等）。"
                            ),
                        )
                    )
                    continue
                logger.error(
                    "[TEXT_TOOLCALL] Agent %s repeatedly emitted text-mode tool calls "
                    "after corrective nudge - failing explicitly.",
                    agent_name,
                )
                raise TextModeToolCallError(
                    "Agent '{}' repeatedly emitted tool calls as plain text instead of "
                    "native tool_calls; the turn is not a trustworthy completed answer.".format(
                        agent_name
                    )
                )

            # Check if LLM wants to call tools
            if not response.tool_calls:
                break

            # Append the assistant's tool_call message BEFORE executing tools
            # This ensures correct message order: assistant(tool_calls) -> tool result
            full_messages.append(
                ChatMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                    reasoning_content=getattr(response, "reasoning_content", None),
                )
            )
            # Also save to session so it persists for next turn
            try:
                session_manager.append_message(
                    session_id, "assistant", response.content or "",
                    tool_calls=response.tool_calls,
                    reasoning_content=getattr(response, "reasoning_content", None),
                )
            except ValueError as e:
                # session may not exist in some modes
                logger.warning(
                    "Failed to persist assistant tool_call message: session not found - session_id=%s, error=%s",
                    session_id,
                    str(e),
                )
            except Exception as e:
                # Persistence failure must not interrupt the tool loop
                logger.error(
                    "Failed to persist assistant tool_call message: session_id=%s, error=%s",
                    session_id,
                    str(e),
                )
            # Mark agent as executing tools
            tool_names = [tc.get("function", {}).get("name", "") for tc in response.tool_calls]
            agent_monitor.update_phase(
                agent_id, "tool_call",
                "Executing {} tool(s): {}".format(len(tool_names), ", ".join(tool_names)),
                progress=0.0,
            )

            # Execute each tool call
            for tc in response.tool_calls:
                tool_call_id = tc.get("id", "")
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                _tool_calls_executed += 1
                _tools_used[tool_name] = _tools_used.get(tool_name, 0) + 1
                tool_args_str = func.get("arguments", "{}")

                # Parse arguments
                try:
                    tool_args = (
                        json.loads(tool_args_str)
                        if isinstance(tool_args_str, str)
                        else tool_args_str
                    )
                except (json.JSONDecodeError, ValueError):
                    tool_args = {}

                # Build ToolContext
                # Calculate current tokens and get context info
                total_tokens = sum(_cached_count_tokens(m.content) for m in full_messages)
                model_metadata = provider_registry.get_model_metadata(model_name)
                context_limit = model_metadata.get("context_window", _DEFAULT_CONTEXT_LIMIT)
                compaction_buffer = model_metadata.get("compaction_buffer", _COMPACTION_BUFFER)

                # Build base_extra dict
                # Include on_tool_call so tools (e.g., task) can send progress notifications
                base_extra = {
                    "workspace": get_workspace(),
                    "on_tool_call": on_tool_call,
                    "context_info": {
                        "current_tokens": total_tokens,
                        "context_limit": context_limit,
                        "compaction_buffer": compaction_buffer,
                    },
                }                # Merge caller-provided extra (e.g., selection_callback for GUI)
                if extra:
                    base_extra.update(extra)

                tool_ctx = ToolContext(
                    session_id=session_id,
                    message_id="tool-{}-{}".format(session_id, tool_call_id),
                    agent=agent_name,
                    abort=abort_event if abort_event is not None else threading.Event(),
                    call_id=tool_call_id,
                    messages=[{"role": m.role, "content": m.content} for m in full_messages],
                    extra=base_extra,
                )

                logger.debug("Tool call: %s(%s) id=%s", tool_name, tool_args_str, tool_call_id)

                # Update agent monitor with current tool name (show tool count too)
                total_tools_in_round = len(response.tool_calls) if response.tool_calls else 1
                current_tool_index = next(
                    (i for i, tc2 in enumerate(response.tool_calls or [])
                     if tc2.get("id", "") == tool_call_id),
                    0,
                )
                agent_monitor.update_phase(
                    agent_id, "tool_call",
                    "Tool {}/{}: {}".format(current_tool_index + 1, total_tools_in_round, tool_name),
                    progress=(current_tool_index + 1) / total_tools_in_round,
                )

                # Check permissions before executing                # If agent has 'full' permission, skip permission checker
                if agent.permission == "full":
                    perm_result = ALLOWED
                else:
                    # Extract path from args for path-based permission checks
                    tool_path = None  # type: Optional[str]
                    if isinstance(tool_args, dict):
                        tool_path = tool_args.get("file_path") or tool_args.get("path")
                    perm_result = permission_checker.check(tool_name, args=tool_args, path=tool_path)

                logger.debug("Permission check for %s: %s (agent permission: %s)", tool_name, perm_result, agent.permission)

                if perm_result == "denied":
                    logger.warning("Tool %s denied by permission policy", tool_name)
                    result = {
                        "error": "Action denied by permission policy",
                        "error_type": "PermissionDenied",
                    }
                elif perm_result == "needs_ask":
                    # Interactive permission request
                    if on_permission_ask is not None:
                        try:
                            allowed = on_permission_ask(tool_name, tool_args)
                        except Exception as e:
                            logger.warning("Permission callback error for %s: %s", tool_name, e)
                            allowed = False  # Fail-closed: callback errors must not bypass approval
                    else:
                        allowed = False  # No callback provided, deny by default for security
                    if allowed:
                        logger.debug("User allowed tool: %s", tool_name)
                        try:
                            tool_obj = tool_registry.get(tool_name)
                            tool_timeout = getattr(tool_obj, "timeout", None)
                        except Exception as e:
                            logger.warning("Failed to get tool timeout for %s: %s", tool_name, e)
                            tool_timeout = None
                        result = tool_registry.execute(
                            tool_name, tool_args, tool_ctx, timeout=tool_timeout
                        )
                        if "error" in result:
                            logger.warning("Tool %s failed: %s", tool_name, result["error"])
                        else:
                            logger.debug("Tool %s succeeded", tool_name)
                    else:
                        logger.info("User denied tool: %s", tool_name)
                        result = {
                            "error": "User denied permission to execute this tool",
                            "error_type": "PermissionDenied",
                        }
                else:
                    # ALLOWED
                    logger.debug("Executing tool: %s", tool_name)
                    try:
                        tool_obj = tool_registry.get(tool_name)
                        tool_timeout = getattr(tool_obj, "timeout", None)
                    except Exception as e:
                        logger.warning("Failed to get tool timeout for %s: %s", tool_name, e)
                        tool_timeout = None
                    result = tool_registry.execute(
                        tool_name, tool_args, tool_ctx, timeout=tool_timeout
                    )
                    if "error" in result:
                        logger.warning("Tool %s failed: %s", tool_name, result["error"])
                    else:
                        logger.debug("Tool %s succeeded", tool_name)

                # Notify callback for progress display
                if on_tool_call is not None:
                    try:
                        logger.debug("Invoking on_tool_call callback for %s", tool_name)
                        on_tool_call(tool_name, tool_args, result)
                        logger.debug("on_tool_call callback completed for %s", tool_name)
                    except Exception as e:
                        logger.warning("Tool callback error for %s: %s", tool_name, e)
                        pass  # Callback errors should not break execution

                # Dispatch plugin on_tool_call hooks
                try:
                    from berserker.plugin_system import plugin_manager
                    plugin_manager.dispatch_tool_call(tool_name, tool_args, result)
                except Exception as e:
                    logger.warning("Plugin on_tool_call hook failed: %s", e)
                    pass  # Plugin errors should not break agent execution

                # Format tool result as ChatMessage
                if "error" in result:
                    tool_content = "<tool_error tool='{}'>{}</tool_error>".format(tool_name, result["error"])
                else:
                    tool_content = result.get("output", "")

                # Auto-truncate large tool outputs to prevent context overflow
                # Read from agent options, fallback to default 64000 chars (~16K tokens)
                agent_options = getattr(agent, 'options', {})
                max_tool_output_chars = 64000
                if isinstance(agent_options, dict):
                    max_tool_output_chars = agent_options.get('max_tool_output_chars', 64000)

                if len(tool_content) > max_tool_output_chars:
                    truncated_len = len(tool_content) - max_tool_output_chars
                    logger.debug(
                        "[EXECUTOR_TRUNCATE] tool=%s, len=%d, max_chars=%d, removing=%d chars",
                        tool_name, len(tool_content), max_tool_output_chars, truncated_len,
                    )
                    tool_content = tool_content[:max_tool_output_chars]
                    tool_content += "\n\n[Output truncated: {} characters omitted. Read specific line ranges or use grep to find relevant sections.]".format(truncated_len)
                    logger.warning(
                        "Tool output truncated for %s: %d chars -> %d chars",
                        tool_name, len(result.get("output", "")), len(tool_content)
                    )
                # Append tool result to messages
                logger.debug(
                    "[EXECUTOR_TOOL_RESULT] Appending tool result: tool=%s, tool_call_id=%s, content_len=%d",
                    tool_name, tool_call_id, len(tool_content) if tool_content else 0,
                )
                if not tool_call_id:
                    logger.error(
                        "[EXECUTOR_TOOL_RESULT_EMPTY_ID] tool_call_id is empty for tool=%s! "
                        "tc keys=%s", tool_name, list(tc.keys()) if tc else "N/A",
                    )
                full_messages.append(
                    ChatMessage(
                        role="tool",
                        content=tool_content,
                        tool_call_id=tool_call_id,
                        name=tool_name,
                    )
                )
                # Persist tool result to session for cross-session switching
                try:
                    session_manager.append_message(
                        session_id,
                        "tool",
                        tool_content,
                        tool_result_for=tool_call_id,
                        tool_name=tool_name,
                    )
                except ValueError as e:
                    logger.warning(
                        "Failed to persist tool result: session not found - session_id=%s, error=%s",
                        session_id,
                        str(e),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to persist tool result: session_id=%s, tool=%s, error=%s",
                        session_id,
                        tool_name,
                        str(e),
                    )

                # Check if this is a skill tool call - if so, inject full SKILL.md content
                # into system prompt for this round only
                if self._manager._is_skill_tool(tool_name, tool_registry):
                    full_messages = self._manager._inject_skill_content(
                        full_messages, tool_name, result, agent
                    )

            # Handle finish_reason exhaustively
            finish_reason = response.finish_reason
            if finish_reason == FINISH_TOOL_CALLS:
                # Continue tool call loop
                pass
            elif finish_reason == FINISH_STOP:
                # Normal completion, exit loop
                break
            elif finish_reason == FINISH_LENGTH:
                # Max tokens reached, exit loop
                logger.warning(
                    "LLM response truncated: max tokens reached (iteration %d)", iteration
                )
                break
            elif finish_reason == FINISH_CONTENT_FILTER:
                # Content blocked by safety filter, exit loop
                logger.warning("LLM response blocked by content filter (iteration %d)", iteration)
                break
            elif finish_reason == FINISH_REFUSAL:
                # Model refused to generate, exit loop
                logger.warning("LLM refused to generate response (iteration %d)", iteration)
                break
            elif finish_reason == FINISH_ERROR:
                # Error during generation, exit loop
                logger.error("LLM error during generation (iteration %d)", iteration)
                break
            elif finish_reason not in VALID_FINISH_REASONS:
                # Unknown finish_reason - log warning and exit loop
                logger.warning(
                    "Unknown finish_reason '%s' (iteration %d), expected one of %s",
                    finish_reason,
                    iteration,
                    VALID_FINISH_REASONS,
                )
                break
            else:
                # Default: exit loop for any other known value
                break

        # Step 5.5: Max steps reached - inject summary prompt and make final tool-less call
        if iteration >= max_tool_iterations and response is not None:
            # Check abort before making final call
            if abort_event is not None and abort_event.is_set():
                logger.info("Execution aborted before max-steps final call")
                agent_id = "{}-{}".format(agent_name, session_id)
                agent_monitor.update_status(agent_id, STATUS_FAILED)
                return {"content": "", "usage": None, "finish_reason": "abort"}
            logger.info(
                "Max tool iterations (%d) reached, injecting max-steps prompt",
                max_tool_iterations,
            )
            max_steps_prompt = _make_max_steps_prompt(max_tool_iterations)
            full_messages.append(ChatMessage(role="user", content=max_steps_prompt))
            agent_monitor.update_phase(agent_id, "responding", "Summarizing (max steps reached)...")
            # Final call with tools disabled - text-only summary
            response = provider.chat(full_messages, model_name)            # Log final max-steps response
            content_preview = (response.content or "")[:200]
            logger.info(
                "Max-steps final response: content_len=%d, preview=%s",
                len(response.content or ""),
                repr(content_preview),
            )

        # Step 6: Persist assistant response
        if response is not None:
            try:
                session_manager.append_message(
                    session_id,
                    "assistant",
                    response.content,
                )
            except ValueError as e:
                logger.error(
                    "Failed to persist assistant response: session not found - session_id=%s, error=%s",
                    session_id,
                    str(e),
                )
            except Exception as e:
                content_preview = repr((response.content or "")[:100])
                logger.error(
                    "Failed to persist assistant response: session_id=%s, content_preview=%s, error=%s",
                    session_id,
                    content_preview,
                    str(e),
                )

        # Step 7: Build response dict
        final_content = response.content if response is not None else ""
        final_finish_reason = response.finish_reason if response is not None else FINISH_STOP
        if _stuck_aborted:
            # Forced stop after a stuck empty-tool loop: surface an explicit
            # error instead of a silent "successful" completion.
            final_finish_reason = FINISH_ERROR
            _stuck_notice = u"检测到工具调用循环停滞，已强制停止"
            final_content = (
                (final_content + "\n\n" + _stuck_notice) if final_content else _stuck_notice
            )
        response_dict = {
            "content": final_content,
            "usage": response.usage
            if response is not None
            else {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "finish_reason": final_finish_reason,
            # Execution stats for callers (task tool annotates subagent runs)
            "tool_calls_count": _tool_calls_executed,
            "tools_used": dict(_tools_used),
            "iterations": iteration,
        }

        # Step 7.5: Dispatch plugin after_execute hooks
        try:
            from berserker.plugin_system import plugin_manager
            plugin_manager.dispatch_after_execute(agent_name, response_dict, session_id)
        except Exception as e:
            logger.warning("Plugin after_execute hook failed: %s", e)
            pass  # Plugin errors should not break agent execution

        # Step 7.6: Update monitor status to completed
        agent_id = "{}-{}".format(agent_name, session_id)
        finish_reason = response_dict.get("finish_reason", "")
        if finish_reason in ("abort", "error"):
            agent_monitor.update_phase(agent_id, "done", "Failed: {}".format(finish_reason), progress=1.0)
            agent_monitor.update_status(agent_id, STATUS_FAILED)
        else:
            # Provide a rich final task summary
            usage = response_dict.get("usage", {}) or {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            agent_monitor.update_phase(
                agent_id, "done",
                "Completed ({} iterations, {} + {} tokens)".format(
                    iteration, prompt_tokens, completion_tokens,
                ),
                progress=1.0,
            )
            agent_monitor.update_status(agent_id, STATUS_COMPLETED)

        # Step 8: Return response content, usage, and finish_reason
        return response_dict
    def _execute_compaction(
        self,
        full_messages,  # type: List[ChatMessage]
        session_id,  # type: str
        agent_name,  # type: str
        model_name,  # type: str
        context_limit,  # type: int
        strategy,  # type: CompactionStrategy
        token_counter,  # type: Any
        tracker=None,  # type: Optional[Any]
        pre_snapshot=None,  # type: Optional[Dict[str, Any]]
        reason="token_overflow",  # type: str
        do_fallback_truncate=False,  # type: bool
        system_msg=None,  # type: Optional[ChatMessage]
    ):
        # type: (...) -> Dict[str, Any]
        """Unified compaction logic that replaces the 3 duplicated copies.

        Handles all 3 use cases:
        - Pre-execution compaction (reason="token_overflow", with snapshot tracking)
        - Loop compaction (reason="loop_token_overflow", with snapshot tracking)
        - ContextLengthExceeded retry compaction (reason="context_retry", no snapshot tracking)

        Args:
            full_messages: Messages to compact.
            session_id: Session ID for persistence.
            agent_name: Agent name for logging.
            model_name: Model name for token counting.
            context_limit: Context window size.
            strategy: CompactionStrategy instance.
            token_counter: TokenCounter instance.
            tracker: Optional SnapshotTracker (None for retry compaction).
            pre_snapshot: Optional pre-snapshot dict (None for retry compaction).
            reason: Event reason string.
            do_fallback_truncate: Whether to apply fallback truncation after compaction.
            system_msg: System message for fallback truncation.

        Returns:
            Dict with: full_messages, tokens_freed, before_tokens, after_tokens
        """
        # 1. Create pre-compaction snapshot (if tracker provided)
        if tracker is not None:
            pre_snapshot = tracker.create_snapshot(session_id)
            if pre_snapshot.get("hash"):
                tracker.save_snapshot(pre_snapshot)
                bus.publish(
                    SNAPSHOT_CREATED,
                    SnapshotCreatedData(
                        session_id=session_id,
                        snapshot_id=pre_snapshot["snapshot_id"],
                        additions=0,
                        deletions=0,
                        files=0,
                    ),
                )

        # 2. Publish COMPACTION_STARTED
        bus.publish(
            COMPACTION_STARTED,
            CompactionStartedData(session_id=session_id, reason=reason),
        )

        # 3. Prune + compact
        before_tokens = token_counter.count_messages(full_messages, model_name)
        full_messages = self._manager.prune_messages(full_messages, session_id=session_id)
        full_messages = self._manager.compact(
            full_messages, max_tokens=context_limit - strategy.config.reserved
        )

        # 4. Calculate tokens after compaction
        after_tokens = token_counter.count_messages(full_messages, model_name)
        tokens_freed = before_tokens - after_tokens

        # 4.5. Fallback truncate if needed (only for pre-execution compaction)
        if do_fallback_truncate and system_msg is not None:
            target_limit = context_limit - strategy.config.reserved
            if after_tokens > target_limit:
                full_messages = self._fallback_truncate_messages(
                    full_messages, system_msg, target_limit, token_counter, model_name
                )
                after_tokens = token_counter.count_messages(full_messages, model_name)
                tokens_freed = before_tokens - after_tokens

        # 5. Create post-compaction snapshot + diff (if tracker + pre_snapshot provided)
        if tracker is not None and pre_snapshot is not None:
            post_snapshot = tracker.create_snapshot(session_id)
            if pre_snapshot.get("hash") and post_snapshot.get("hash"):
                diff = tracker.diff_snapshots(pre_snapshot["hash"], post_snapshot["hash"])
                diff_stats = tracker.get_diff_stats(diff)
                tracker.save_snapshot(post_snapshot, **diff_stats)
                bus.publish(
                    SNAPSHOT_CREATED,
                    SnapshotCreatedData(
                        session_id=session_id,
                        snapshot_id=post_snapshot["snapshot_id"],
                        additions=diff_stats.get("additions", 0),
                        deletions=diff_stats.get("deletions", 0),
                        files=diff_stats.get("files", 0),
                    ),
                )

        # 6. Publish COMPACTION_COMPLETED
        bus.publish(
            COMPACTION_COMPLETED,
            CompactionCompletedData(
                session_id=session_id,
                tokens_before=before_tokens,
                tokens_after=after_tokens,
                tokens_freed=tokens_freed,
            ),
        )

        # Log compaction results
        logger.info(
            "Compaction complete: %d tokens freed (%d -> %d), %d messages reduced to %d",
            tokens_freed,
            before_tokens,
            after_tokens,
            len(full_messages) + 1,
            len(full_messages),
        )

        # 7. Persist compacted messages
        try:
            compacted_dicts = [_chat_message_to_dict(m) for m in full_messages]
            session_manager.replace_messages(session_id, compacted_dicts)
            logger.info(
                "Persisted %d compacted messages to database for session %s",
                len(compacted_dicts),
                session_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to persist compacted messages: session_id=%s, error=%s",
                session_id,
                str(e),
            )

        # 8. Persist agent config
        try:
            session_manager.save_agent_config(session_id, agent_name)
            logger.info(
                "Persisted agent config '%s' for session %s after compaction",
                agent_name,
                session_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to persist agent config after compaction: session_id=%s, error=%s",
                session_id,
                str(e),
            )

        # 9. Return results
        return {
            "full_messages": full_messages,
            "tokens_freed": tokens_freed,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
        }

    def _fallback_truncate_messages(
        self, messages, system_msg, max_tokens, token_counter, model_name
    ):
        # type: (List[ChatMessage], ChatMessage, int, Any, str) -> List[ChatMessage]
        """Fallback truncation when AI compaction still exceeds token limits.

        Removes the oldest non-system messages until the total token count
        falls within max_tokens, while always preserving the system prompt.
        Uses binary search to efficiently find the optimal truncation point.

        Args:
            messages: List of ChatMessage objects (may include system_msg at index 0).
            system_msg: The system prompt ChatMessage to always preserve.
            max_tokens: Maximum allowed token count for the result.
            token_counter: TokenCounter instance for counting tokens.
            model_name: Model name for token counting.

        Returns:
            Truncated list of ChatMessage objects with system_msg at index 0.
        """
        # Separate system from non-system messages
        non_system = [m for m in messages if m.role != "system"]

        if not non_system:
            # Only system messages remain -- nothing to truncate
            return [system_msg]

        # Binary search for the minimum number of messages to drop from the front
        # We want to keep messages[lo:] such that token_count <= max_tokens
        lo = 0  # drop nothing
        hi = len(non_system)  # drop everything

        # Precompute token counts for each message to avoid recounting
        msg_tokens = []  # type: List[int]
        for msg in non_system:
            msg_tokens.append(token_counter.count_messages([msg], model_name))

        # Binary search: find smallest `drop` such that kept tokens <= max_tokens
        best_drop = len(non_system)  # worst case: drop all

        while lo <= hi:
            mid = (lo + hi) // 2
            # Keep non_system[mid:] (drop first `mid` messages)
            kept_tokens = sum(msg_tokens[mid:])
            if kept_tokens <= max_tokens:
                best_drop = mid
                hi = mid - 1  # try dropping fewer
            else:
                lo = mid + 1  # need to drop more

        # Ensure we keep at least the last 5 messages if possible
        min_keep = 5
        max_drop = max(0, len(non_system) - min_keep)
        if best_drop > max_drop:
            # Check if keeping min_keep messages is within limit
            kept_tokens = sum(msg_tokens[max_drop:])
            if kept_tokens <= max_tokens:
                best_drop = max_drop
            # If even keeping min_keep exceeds limit, use best_drop as-is

        kept = non_system[best_drop:]
        result = [system_msg] + kept

        dropped_count = len(non_system) - len(kept)
        logger.warning(
            "Fallback truncation: dropped %d oldest messages, kept %d (%d tokens, limit %d)",
            dropped_count,
            len(kept),
            sum(msg_tokens[best_drop:]) if best_drop < len(msg_tokens) else 0,
            max_tokens,
        )

        return result
