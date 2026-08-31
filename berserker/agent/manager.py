"""

Agent system for berserker — agent definitions, dispatch, and conversation management.



Provides:

- AgentInfo dataclass for agent configuration

- 7 built-in agents (build, plan, general, explore, compaction, title, summary)

- AgentManager class for agent lifecycle and execution

- Message compaction for long conversations



Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.

"""



from __future__ import annotations



import copy

import hashlib

import json

import logging

import os

import platform

import threading

import time

import uuid

import warnings

from typing import Any, Callable, Dict, List, Optional



from berserker.agent.base import BuiltInAgent, CustomAgent

from berserker.agent.compaction import (

    CompactionConfig,

    CompactionStrategy,

    default_config,

    load_compaction_config,

)

from berserker.agent.constants import (

    _COMPACTION_BUFFER,

    _DEFAULT_CONTEXT_LIMIT,

    PRUNE_MINIMUM,

    PRUNE_PROTECT,

    _chat_message_to_dict,

    _make_max_steps_prompt,

)

from berserker.agent.executor import AgentExecutor



logger = logging.getLogger(__name__)

from berserker.agent.factory import (

    create_agent_from_schema,

    create_builtin_agent,

)

from berserker.agent.prompts import (
    _SYSTEM_PROMPT_BUILD,
    _SYSTEM_PROMPT_COMPACTION,
    _SYSTEM_PROMPT_EXPLORE,
    _SYSTEM_PROMPT_GENERAL,
    _SYSTEM_PROMPT_PLAN,
    _SYSTEM_PROMPT_SUMMARY,
    _SYSTEM_PROMPT_TITLE,
    _SYSTEM_PROMPT_CONSULTANT,
    _SYSTEM_PROMPT_CRITIC,
    _SYSTEM_PROMPT_ORCHESTRATOR,
)

from berserker.agent.messaging import AgentMessage, message_router

from berserker.agent.prompt_loader import PromptLoader

from berserker.agent.registry import AgentRegistry

from berserker.agent.registry import registry as agent_registry

from berserker.bus import (

    AGENT_MESSAGE_FAILED,

    AGENT_MESSAGE_RECEIVED,

    AGENT_MESSAGE_SENT,

    COMPACTION_COMPLETED,

    COMPACTION_STARTED,

    PRUNING_COMPLETED,

    SNAPSHOT_CREATED,

    CompactionCompletedData,

    CompactionStartedData,

    PruningCompletedData,

    SnapshotCreatedData,

    bus,

)

from berserker.permission import ALLOWED, DENIED, NEEDS_ASK, permission_checker

from berserker.provider.base import (

    FINISH_CONTENT_FILTER,

    FINISH_ERROR,

    FINISH_LENGTH,

    FINISH_REFUSAL,

    FINISH_STOP,

    FINISH_TOOL_CALLS,

    VALID_FINISH_REASONS,

    ChatMessage,

    ChatResponse,

    ContextLengthExceeded,

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



logger = logging.getLogger(__name__)





# ---------------------------------------------------------------------------

# Prompt Loading with Fallback

# ---------------------------------------------------------------------------



# Inline fallback prompts — used when external files are not found.
_INLINE_PROMPTS = {
    "berserker": _SYSTEM_PROMPT_BUILD,
    "plan": _SYSTEM_PROMPT_PLAN,
    "general": _SYSTEM_PROMPT_GENERAL,
    "explore": _SYSTEM_PROMPT_EXPLORE,
    "compaction": _SYSTEM_PROMPT_COMPACTION,
    "title": _SYSTEM_PROMPT_TITLE,
    "summary": _SYSTEM_PROMPT_SUMMARY,
    "consultant": _SYSTEM_PROMPT_CONSULTANT,
    "critic": _SYSTEM_PROMPT_CRITIC,
    "executor": _SYSTEM_PROMPT_ORCHESTRATOR,
}  # type: Dict[str, str]



# _chat_message_to_dict moved to constants.py to break circular import





def _load_system_prompt(name):

    # type: (str) -> str

    """Load a system prompt, preferring external file with inline fallback.



    Tries to load from the external prompts/ directory first.

    If the file is not found, falls back to the inline constant from factory.py.



    Args:

        name: Prompt name (e.g., "build", "plan").



    Returns:

        The prompt text from external file or inline fallback.

    """

    try:

        loader = PromptLoader()

        return loader.load(name)

    except FileNotFoundError:

        logger.debug("External prompt '%s' not found, using inline fallback", name)

        return _INLINE_PROMPTS.get(name, "")





# ---------------------------------------------------------------------------

# AgentInfo Dataclass

# ---------------------------------------------------------------------------





class AgentInfo(object):

    """Configuration for a single agent.



    Attributes:

        name: Unique identifier for the agent (e.g. 'build', 'plan').

        mode: Agent mode — 'primary', 'subagent', or 'hidden'.

        model: Model identifier this agent uses (e.g. 'gpt-4', 'claude-3.5-sonnet').

        system_prompt: System prompt that defines the agent's behavior.

        tools: List of tool IDs this agent is allowed to use.

        description: Optional human-readable description of the agent's purpose.

        permission: Permission level — 'full' (all tools allowed) or 'restricted'

                    (tools must pass permission checks).

        max_tool_iterations: Maximum tool call loop iterations per turn (default: 100000).

    """



    def __init__(

        self,

        name,  # type: str

        mode,  # type: str

        model,  # type: str

        system_prompt,  # type: str

        tools,  # type: List[str]

        description=None,  # type: Optional[str]

        permission="full",  # type: str

        max_tool_iterations=None,  # type: Optional[int]

    ):

        # type: (...) -> None

        warnings.warn(

            "AgentInfo is deprecated. Use AgentSchema + BaseAgent instead. "

            "See berserker.agent.schema.AgentSchema and berserker.agent.base.BaseAgent.",

            DeprecationWarning,

            stacklevel=2,

        )

        self.name = name

        self.mode = mode

        self.model = model

        self.system_prompt = system_prompt

        self.tools = tools

        self.description = description

        self.permission = permission

        self.max_tool_iterations = (

            max_tool_iterations if max_tool_iterations is not None else _DEFAULT_MAX_TOOL_ITERATIONS

        )



    def __repr__(self):

        # type: () -> str

        return "AgentInfo(name={!r}, mode={!r}, model={!r})".format(

            self.name, self.mode, self.model

        )



    def __eq__(self, other):

        # type: (Any) -> bool

        if not isinstance(other, AgentInfo):

            return False

        return (

            self.name == other.name

            and self.mode == other.mode

            and self.model == other.model

            and self.system_prompt == other.system_prompt

            and self.tools == other.tools

            and self.description == other.description

            and self.permission == other.permission

            and self.max_tool_iterations == other.max_tool_iterations

        )





# ---------------------------------------------------------------------------

# Built-in Agent Definitions

# ---------------------------------------------------------------------------



# Tool ID constants — these match the tool registry IDs used across berserker.

_ALL_TOOL_IDS = [

    "read",

    "write",

    "edit",

    "multiedit",

    "apply_patch",

    "bash",

    "ls",

    "glob",

    "grep",

    "skill",

    "git",

    "todo",

    "selection",

    "task",

    "mkdir",

    "rmdir",

    "mv",

    "cp",

    "rm",

    "touch",

    "lsp",

]  # type: List[str]

_READ_ONLY_TOOL_IDS = [

    "read",

    "ls",

    "glob",

    "grep",

]  # type: List[str]



_MINIMAL_TOOL_IDS = [

    "read",

]  # type: List[str]



# Sub-agents: exclude user-interactive tools

_SUBAGENT_TOOL_IDS = [t for t in _ALL_TOOL_IDS if t not in ("todo", "selection", "task")]  # type: List[str]



import platform





def _make_os_environment_prompt():  # type: () -> str

    """Generate runtime OS environment information for system prompts.



    Provides the LLM with current OS details so it can generate correct

    platform-specific commands (e.g. PowerShell vs bash, path separators, etc.).



    Returns:

        A string describing the current runtime environment.

    """

    system = platform.system()

    release = platform.release()

    machine = platform.machine()

    python_ver = platform.python_version()

    cwd = os.getcwd()



    parts = [

        "## Runtime Environment",

        "- OS: {} {} ({})".format(system, release, machine),

        "- Python: {}".format(python_ver),

        "- Working directory: {}".format(cwd),

    ]



    if system == "Windows":

        parts.append("- Shell: PowerShell (use `;` for command chaining, NOT `&&`)")

        parts.append("- Path separator: backslash `\\`")

        parts.append("- Line endings: CRLF")

        parts.append(

            "- Note: Use `workdir` parameter for directory changes instead of `cd && command`"

        )

    elif system == "Linux":

        parts.append("- Shell: bash")

        parts.append("- Path separator: forward slash `/`")

        parts.append("- Line endings: LF")

    elif system == "Darwin":

        parts.append("- Shell: zsh")

        parts.append("- Path separator: forward slash `/`")

        parts.append("- Line endings: LF")



    return "\n".join(parts)





# Default model used by agents when no specific model is configured.

_DEFAULT_MODEL = "gpt-4o"



# Model context window limits (tokens). Used for auto-trigger compaction.

# Maps model name to its context window size. Unknown models default to 128K.

_MODEL_CONTEXT_LIMITS = {

    "gpt-4o": 128000,

    "gpt-4o-mini": 128000,

    "gpt-4-turbo": 128000,

    "gpt-3.5-turbo": 16385,

    "o1": 200000,

    "o3-mini": 200000,

    "claude-sonnet-4-20250514": 200000,

    "claude-3.5-sonnet-20241022": 200000,

    "claude-3.5-haiku-20241022": 200000,

    "claude-3-opus-20240229": 200000,

    "gemini-2.0-flash": 1000000,

    "gemini-1.5-pro": 2000000,

    "gemini-1.5-flash": 1000000,

    "mistral-large-latest": 128000,

    "mistral-small-latest": 128000,

    "open-mistral-nemo": 128000,

}  # type: Dict[str, int]



# Model compaction buffers (tokens). Used for auto-trigger compaction.

# Maps model name to its compaction buffer size. Unknown models default to 20K.

_MODEL_COMPACTION_BUFFERS = {

    "gpt-4o": 20000,

    "gpt-4o-mini": 20000,

    "gpt-4-turbo": 20000,

    "gpt-3.5-turbo": 20000,

    "o1": 20000,

    "o3-mini": 20000,

    "claude-sonnet-4-20250514": 20000,

    "claude-3.5-sonnet-20241022": 20000,

    "claude-3.5-haiku-20241022": 20000,

    "claude-3-opus-20240229": 20000,

    "gemini-2.0-flash": 20000,

    "gemini-1.5-pro": 20000,

    "gemini-1.5-flash": 20000,

    "mistral-large-latest": 20000,

    "mistral-small-latest": 20000,

    "open-mistral-nemo": 20000,

}  # type: Dict[str, int]



# _DEFAULT_CONTEXT_LIMIT and _COMPACTION_BUFFER moved to constants.py



# Default max tool call loop iterations per agent turn.# Can be overridden per-agent via config or AgentInfo constructor.

_DEFAULT_MAX_TOOL_ITERATIONS = 100000



# Pruning thresholds come from the compaction config (see prune_messages).



# Suppress deprecation warnings for built-in agents (internal use only)

with warnings.catch_warnings():

    warnings.simplefilter("ignore", DeprecationWarning)

    BUILT_IN_AGENTS_CONFIG = [

        AgentInfo(
            name="berserker",
            mode="primary",
            model=_DEFAULT_MODEL,
            system_prompt=_load_system_prompt("berserker"),
            tools=list(_ALL_TOOL_IDS),
            description="Primary coding agent for implementation tasks",
            permission="full",
        ),
        AgentInfo(
            name="plan",
            mode="primary",
            model=_DEFAULT_MODEL,
            system_prompt=_load_system_prompt("plan"),
            tools=list(_READ_ONLY_TOOL_IDS),
            description="Code analysis and planning agent",
            permission="restricted",
        ),        AgentInfo(

            name="general",

            mode="subagent",

            model=_DEFAULT_MODEL,

            system_prompt=_load_system_prompt("general"),

            tools=list(_SUBAGENT_TOOL_IDS),
            description="General-purpose subagent for complex multi-step tasks",

            permission="full",

        ),

        AgentInfo(

            name="explore",

            mode="subagent",

            model=_DEFAULT_MODEL,

            system_prompt=_load_system_prompt("explore"),

            tools=list(_READ_ONLY_TOOL_IDS),

            description="Codebase exploration and discovery subagent",

            permission="restricted",

        ),

        AgentInfo(

            name="compaction",

            mode="hidden",

            model=_DEFAULT_MODEL,

            system_prompt=_load_system_prompt("compaction"),

            tools=list(_READ_ONLY_TOOL_IDS),

            description="Hidden agent for compressing conversation history",

            permission="restricted",

        ),

        AgentInfo(

            name="title",

            mode="hidden",

            model=_DEFAULT_MODEL,

            system_prompt=_load_system_prompt("title"),

            tools=list(_MINIMAL_TOOL_IDS),

            description="Hidden agent for generating session titles",

            permission="restricted",

        ),

        AgentInfo(
            name="summary",
            mode="hidden",
            model=_DEFAULT_MODEL,
            system_prompt=_load_system_prompt("summary"),
            tools=list(_READ_ONLY_TOOL_IDS),
            description="Hidden agent for generating session summaries",
            permission="restricted",
        ),
        AgentInfo(
            name="consultant",
            mode="subagent",
            model=_DEFAULT_MODEL,
            system_prompt=_load_system_prompt("consultant"),
            tools=["read", "ls", "glob", "grep", "lsp"],
            description="Pre-planning consultant that analyzes requests to identify hidden intentions, ambiguities, and AI failure points.",
            permission="restricted",
        ),
        AgentInfo(
            name="critic",
            mode="subagent",
            model=_DEFAULT_MODEL,
            system_prompt=_load_system_prompt("critic"),
            tools=["read", "ls", "glob", "grep"],
            description="Expert reviewer for evaluating work plans against rigorous clarity, verifiability, and completeness standards.",
            permission="restricted",
        ),
        AgentInfo(
            name="executor",
            mode="primary",
            model=_DEFAULT_MODEL,
            system_prompt=_load_system_prompt("executor"),
            tools=[tool for tool in _ALL_TOOL_IDS if tool != "task"],
            description="Master orchestrator agent that coordinates specialized agents to complete todo lists.",
            permission="full",
        ),    ]  # type: List[AgentInfo]

# List of built-in agent names for export.

BUILT_IN_AGENTS = [agent.name for agent in BUILT_IN_AGENTS_CONFIG]  # type: List[str]





# ---------------------------------------------------------------------------

# AgentManager

# ---------------------------------------------------------------------------






def _msg_token_count(msg):
    # type: (Any) -> int
    """Full per-message token count: content + tool-call arguments +
    reasoning_content (all of these are sent to the API)."""
    n = count_tokens(msg.content or "")
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        args = fn.get("arguments")
        if args:
            n += count_tokens(args if isinstance(args, str) else json.dumps(args, ensure_ascii=False))
    rc = getattr(msg, "reasoning_content", None)
    if rc:
        n += count_tokens(rc)
    return n


def _split_turns(messages):
    # type: (List[Any]) -> List[List[Any]]
    """Group messages into turns; a turn starts at each user message.

    Messages before the first user message form a leading prelude group.
    Turn boundaries never split an assistant(tool_calls)/tool group, so a
    kept tail is always API-safe.
    """
    turns = []
    current = []
    for m in messages:
        if m.role == "user" and current:
            turns.append(current)
            current = []
        current.append(m)
    if current:
        turns.append(current)
    return turns


class AgentManager(object):

    """Manages agent configurations and execution.



    Provides lookup, listing, and execution of agents.

    Built-in agents are registered on initialization.



    Usage:

        manager = AgentManager()

        agent = manager.get("build")

        response = manager.execute("build", messages, session_id, tool_registry)

    """



    def __init__(self):

        # type: () -> None

        """Initialize the agent manager with built-in agents.



        Registers built-in agents into both the internal _agents dict

        (for backward-compatible execute() path) and the global AgentRegistry

        (for new schema-driven API).

        """

        self._agents = {}  # type: Dict[str, AgentInfo]

        self._base_agents = {}  # type: Dict[str, Any]  # BaseAgent instances

        self._compaction_config = default_config()  # type: CompactionConfig

        os_env_prompt = _make_os_environment_prompt()

        for agent in BUILT_IN_AGENTS_CONFIG:

            # Inject runtime OS environment into agent's system prompt

            if "{os_environment}" in agent.system_prompt:

                enriched_prompt = agent.system_prompt.replace("{os_environment}", os_env_prompt)

                # Create a new AgentInfo with the enriched prompt (internal use, suppress warning)

                with warnings.catch_warnings():

                    warnings.simplefilter("ignore", DeprecationWarning)

                    enriched_agent = AgentInfo(

                        name=agent.name,

                        mode=agent.mode,

                        model=agent.model,

                        system_prompt=enriched_prompt,

                        tools=list(agent.tools),

                        description=agent.description,

                        permission=agent.permission,

                        max_tool_iterations=agent.max_tool_iterations,

                    )

                self._agents[agent.name] = enriched_agent

            else:

                self._agents[agent.name] = agent



            # Also create and register BaseAgent instance in the global registry

            base_agent = create_builtin_agent(agent.name)

            self._base_agents[agent.name] = base_agent

            # Only register if not already present (handles multiple AgentManager instances)

            if not agent_registry.has(agent.name):

                agent_registry.register(base_agent)



        # Create executor for handling agent execution logic

        self._executor = AgentExecutor(self)



    def get(self, name):  # type: (str) -> AgentInfo

        """Get an agent configuration by name.



        Args:

            name: The agent identifier.



        Returns:

            The AgentInfo for the requested agent.



        Raises:

            KeyError: If the agent is not found.

        """

        if name not in self._agents:

            raise KeyError(

                "Agent '{}' not found. Available agents: {}".format(

                    name, ", ".join(sorted(self._agents.keys()))

                )

            )

        return self._agents[name]



    def list(self):

        # type: () -> List[AgentInfo]

        """List all registered agents.



        Returns:

            List of all AgentInfo objects.

        """

        return list(self._agents.values())



    def list_primary(self):

        # type: () -> List[AgentInfo]

        """List agents with mode 'primary'.



        Returns:

            List of primary AgentInfo objects.

        """

        return [a for a in self._agents.values() if a.mode == "primary"]



    def list_subagents(self):

        # type: () -> List[AgentInfo]

        """List agents with mode 'subagent'.



        Returns:

            List of subagent AgentInfo objects.

        """

        return [a for a in self._agents.values() if a.mode == "subagent"]



    def get_agent(self, name):

        # type: (str) -> Any

        """Get a BaseAgent instance by name from the registry.



        This is the new API method that returns BaseAgent instances

        (as opposed to get() which returns AgentInfo for backward compat).



        Args:

            name: The agent identifier.



        Returns:

            The BaseAgent instance with the given name.



        Raises:

            AgentNotFoundError: If the agent is not found.

        """

        return agent_registry.get(name)



    def _is_skill_tool(self, tool_name, tool_registry):  # type: (str, Any) -> bool

        """Check if a tool is a skill (SkillAsTool instance).



        Args:

            tool_name: The tool ID.

            tool_registry: The ToolRegistry instance.



        Returns:

            True if the tool is a skill, False otherwise.

        """

        try:

            from berserker.tool.skill import SkillAsTool



            tool_obj = tool_registry.get(tool_name)

            return isinstance(tool_obj, SkillAsTool)

        except Exception as e:

            logger.warning("Skill tool check failed for '%s': %s", tool_name, e)

            return False



    def _inject_skill_content(self, messages, tool_name, result, agent):

        # type: (List[ChatMessage], str, Dict[str, Any], Any) -> List[ChatMessage]

        """Inject full SKILL.md content into system prompt for this round.



        When a skill tool is called, we need to inject the complete skill content

        into the system prompt so the LLM has the full instructions.



        Args:

            messages: Current message list.

            tool_name: The skill tool name.

            result: The tool execution result dict.

            agent: The agent configuration.



        Returns:

            Updated message list with skill content injected into system message.

        """

        # Get skill content from result

        skill_content = result.get("output", "")

        if not skill_content:

            return messages



        # Find the system message (first message)

        for i, msg in enumerate(messages):

            if msg.role == "system":

                # Append skill content to system prompt

                skill_injection = (

                    '\n\n---\n\n<skill_instructions name="{}">\n{}\n</skill_instructions>'

                ).format(tool_name, skill_content)

                messages[i] = ChatMessage(

                    role="system",

                    content=msg.content + skill_injection,

                )

                logger.info("Injected skill content '%s' into system prompt", tool_name)

                break



        return messages



    def execute(

        self,

        agent_name,

        messages,

        session_id,

        tool_registry,

        on_tool_call=None,

        on_permission_ask=None,

        extra=None,

        abort_event=None,

    ):

        # type: (str, List[ChatMessage], str, ToolRegistry, Optional[Callable[[str, Dict[str, Any], Dict[str, Any]], None]], Optional[Callable[[str, Dict[str, Any]], bool]], Optional[Dict[str, Any]], Optional[threading.Event]) -> Dict[str, Any]

        """Run a single agent turn with tool call loop support.



        Executes the specified agent against the given message history:

        1. Get agent configuration

        2. Get provider for the agent's model

        3. Filter tools to agent's allowed list

        4. Call provider.chat() with system prompt + messages + tools

        5. If response indicates tool calls, execute them via tool_registry

        6. Append tool results as tool messages and loop (max 10 iterations)

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



        Returns:

            Dict with keys:

                - 'content': The assistant's text response (str).

                - 'usage': Token usage dict with keys: prompt_tokens, completion_tokens, total_tokens.



        Raises:

            KeyError: If the agent is not found.

            ModelNotFoundError: If the agent's model has no provider.

        """

        # Delegate to the executor for all execution logic

        return self._executor.execute(

            agent_name=agent_name,

            messages=messages,

            session_id=session_id,

            tool_registry=tool_registry,

            on_tool_call=on_tool_call,

            on_permission_ask=on_permission_ask,

            extra=extra,

            abort_event=abort_event,

        )



    def prune_messages(self, messages, session_id=None):

        # type: (List[ChatMessage], Optional[str]) -> List[ChatMessage]

        """Strip old tool call outputs while protecting recent ones.



        Walks messages backward from the end, tracking cumulative token count

        of tool call outputs. Protects the last prune_protect tokens of tool

        call content. Skips pruning 'skill' tool outputs entirely.



        Pruning only runs when enabled in the compaction config (prune=true)

        and total prunable content exceeds prune_minimum tokens.

        Marks pruned tool calls with a _pruned flag to avoid re-pruning.



        When session_id is provided, persists pruning state to the database

        and publishes a PruningCompleted event.



        Args:

            messages: List of ChatMessage objects to potentially prune.

            session_id: Optional session identifier for DB persistence.



        Returns:

            New list of ChatMessage objects with pruned tool outputs.

        """

        # Load already-pruned message IDs from database (if session_id provided)

        already_pruned_ids = set()  # type: set

        if session_id is not None:

            db = get_db()

            rows = db.fetchall(

                "SELECT message_id FROM pruning_state WHERE session_id = ?",

                (session_id,),

            )

            already_pruned_ids = {row["message_id"] for row in rows}



        # First pass: identify tool messages and calculate total prunable tokens

        tool_msg_indices = []  # type: List[tuple]

        total_prunable = 0  # type: int



        for i, msg in enumerate(messages):

            if msg.role == "tool" and not getattr(msg, "_pruned", False):

                # Skip skill tool outputs — always keep them

                if msg.name == "skill":

                    continue

                token_count = count_tokens(msg.content)

                tool_msg_indices.append((i, token_count))

                total_prunable += token_count



        # Only prune when the compaction config enables it and there is

        # enough content to make it worthwhile

        strategy = CompactionStrategy(self._compaction_config)

        if not strategy.should_prune(total_prunable):

            return list(messages)



        # Second pass: walk backward, protect last prune_protect tokens

        prune_protect = strategy.get_prune_protect()

        result = list(messages)  # type: List[ChatMessage]

        protected_tokens = 0  # type: int

        pruned_count = 0  # type: int

        tokens_freed = 0  # type: int

        pruning_records = []  # type: List[dict]



        # Walk backward from most recent tool message

        for idx, token_count in reversed(tool_msg_indices):

            if protected_tokens + token_count <= prune_protect:

                # This message is within the protected window

                protected_tokens += token_count

            else:

                # Prune this message's content

                original_msg = result[idx]



                # Check if already pruned in DB

                msg_id = hashlib.md5(original_msg.content.encode()).hexdigest()[:12]

                if msg_id in already_pruned_ids:

                    continue



                pruned_msg = ChatMessage(

                    role=original_msg.role,

                    content="[Tool output pruned to save context]",

                    tool_call_id=original_msg.tool_call_id,

                    name=original_msg.name,

                )

                # Mark as pruned to avoid re-pruning

                pruned_msg._pruned = True  # type: ignore[attr-defined]

                result[idx] = pruned_msg



                # Track pruning stats

                pruned_count += 1

                tokens_freed += token_count



                # Record for DB persistence

                if session_id is not None:

                    original_content_hash = hashlib.sha256(

                        original_msg.content.encode()

                    ).hexdigest()[:16]

                    pruning_records.append(

                        {

                            "id": uuid.uuid4().hex[:12],

                            "session_id": session_id,

                            "message_id": msg_id,

                            "original_content_hash": original_content_hash,

                            "pruned_content_length": len(pruned_msg.content),

                            "original_content_length": len(original_msg.content),

                            "pruned_at": int(time.time()),

                        }

                    )



        # Persist pruning state to database

        if session_id is not None and pruning_records:

            db = get_db()

            for record in pruning_records:

                insert(db, "pruning_state", record)

            db.commit()



        # Publish pruning completed event

        if session_id is not None and pruned_count > 0:

            bus.publish(

                PRUNING_COMPLETED,

                PruningCompletedData(

                    session_id=session_id,

                    tokens_freed=tokens_freed,

                    messages_pruned=pruned_count,

                ),

            )



        return result



    def compact(self, messages, max_tokens=4096, abort_event=None):
        # type: (List[ChatMessage], int, Any) -> List[ChatMessage]
        """Compress conversation history (hybrid strategy).

        - Under budget: returned unchanged.
        - Otherwise the OLDER turns are summarized by the compaction agent
          while the most recent ``keep_last_turns`` turns are kept verbatim
          (tail capped at 30% of the budget).
        - Guarantee: the result never exceeds *max_tokens* -- the tail is
          shed turn by turn, then the summary itself is truncated.

        Args:
            messages: List of ChatMessage objects to compact.
            max_tokens: Maximum token budget (default: 4096).
            abort_event: Optional threading.Event forwarded to the compaction
                         agent's execute call so a user abort also cancels
                         the summarization pass.

        Returns:
            Compacted list of ChatMessage objects.
        """
        estimated_tokens = sum(_msg_token_count(m) for m in messages)

        if estimated_tokens < max_tokens:
            return list(messages)

        # Separate system messages from conversation
        system_messages = []  # type: List[ChatMessage]
        non_system_messages = []  # type: List[ChatMessage]
        for msg in messages:
            if msg.role == "system":
                system_messages.append(msg)
            else:
                non_system_messages.append(msg)

        if len(non_system_messages) <= 2:
            # Not enough messages to compact meaningfully
            return list(messages)

        keep = int(getattr(self._compaction_config, "keep_last_turns", 2) or 0)
        turns = _split_turns(non_system_messages)

        # Tail selection: newest `keep` turns, capped at 30% of the budget.
        tail_budget = max(1000, int(max_tokens * 0.3))
        tail = []  # type: List[List[ChatMessage]]
        tail_tokens = 0
        for turn in reversed(turns):
            t = sum(_msg_token_count(m) for m in turn)
            if len(tail) >= keep or (tail and tail_tokens + t > tail_budget):
                break
            tail.insert(0, turn)
            tail_tokens += t
        old_turns = turns[: len(turns) - len(tail)]
        old_msgs = [m for turn in old_turns for m in turn]

        summary_content = ""
        if old_msgs:
            # Try AI-driven compaction of the OLDER part via the compaction agent
            try:
                compaction_prompt = (
                    "Provide a detailed prompt for continuing our conversation above.\n"
                    "Focus on information that would be helpful for continuing the conversation, "
                    "including what we did, what we're doing, which files we're working on, and "
                    "what we're going to do next.\n"
                    "The summary that you construct will be used so that another agent can read it "
                    "and continue the work. The most recent turns are kept verbatim and are NOT "
                    "part of this summary.\n\n"
                    "When constructing the summary, try to stick to this template:\n"
                    "---\n"
                    "## Goal\n\n"
                    "[What goal(s) is the user trying to accomplish?]\n\n"
                    "## Instructions\n\n"
                    "- [What important instructions did the user give you that are relevant]\n"
                    "- [If there is a plan or spec, include information about it so next agent can continue using it]\n\n"
                    "## Discoveries\n\n"
                    "[What notable things were learned during this conversation that would be useful "
                    "for the next agent to know when continuing the work]\n\n"
                    "## Accomplished\n\n"
                    "[What work has been completed, what work is still in progress, and what work is left?]\n\n"
                    "## Relevant files / directories\n\n"
                    "[Construct a structured list of relevant files that have been read, edited, or created "
                    "that pertain to the task at hand. If all the files in a directory are relevant, "
                    "include the path to the directory.]\n"
                    "---"
                )
                compaction_result = self.execute(
                    agent_name="compaction",
                    messages=old_msgs
                    + [ChatMessage(role="user", content=compaction_prompt)],
                    session_id="compaction-internal",
                    tool_registry=ToolRegistry(),  # Empty registry — compaction uses read-only tools
                    on_tool_call=None,
                    abort_event=abort_event,
                )
                summary_content = (compaction_result.get("content") or "").strip()
            except Exception as exc:
                logger.warning("AI compaction failed, falling back to truncation: %s", exc)

        def _assemble(tail_turns):
            result = list(system_messages)
            if summary_content:
                # 'assistant' role avoids multiple-system-message issues with OpenAI
                result.append(ChatMessage(role="assistant", content=summary_content))
            for turn in tail_turns:
                result.extend(turn)
            return result

        if not summary_content and old_msgs:
            summary_content = (
                "[Previous conversation summarized: {} messages compressed to save tokens]".format(
                    len(old_msgs)
                )
            )

        result = _assemble(tail)
        # Guarantee pass: shed tail turns (oldest first) until within budget.
        while tail and sum(_msg_token_count(m) for m in result) > max_tokens:
            tail = tail[1:]
            result = _assemble(tail)
        if sum(_msg_token_count(m) for m in result) > max_tokens:
            # Even the summary alone is over budget -- truncate its text.
            over = sum(_msg_token_count(m) for m in result)
            keep_chars = max(2000, len(summary_content) * max_tokens // max(over, 1))
            result = list(system_messages) + [
                ChatMessage(
                    role="assistant",
                    content=summary_content[:keep_chars] + "\n\u2026[summary truncated]",
                )
            ]
        return result


    # ---------------------------------------------------------------------------

    # Config-Based Loading (Phase 3)

    # ---------------------------------------------------------------------------



    def load_from_file(self, config_path):

        # type: (str) -> int

        """Load agents from a JSON/JSONC config file and register them.



        Built-in agents are preserved. Config agents are added as CustomAgent

        instances. If an agent with the same name already exists, it is skipped

        with a warning (built-in agents take precedence).



        Args:

            config_path: Path to the JSON/JSONC config file.



        Returns:

            Number of agents successfully registered.

        """

        from berserker.agent.config import load_agents_from_config

        from berserker.agent.factory import create_agent_from_schema



        try:

            schemas = load_agents_from_config(config_path)

        except (ValueError, FileNotFoundError) as e:

            logger.warning("Failed to load config from '%s': %s", config_path, str(e))

            return 0



        count = 0

        for schema in schemas:

            if agent_registry.has(schema.name):

                logger.warning(

                    "Agent '%s' already registered, skipping config definition",

                    schema.name,

                )

                continue

            try:

                # Mark as non-native (user-defined)

                schema.native = False

                base_agent = create_agent_from_schema(schema)

                self.register(base_agent)

                count += 1

            except Exception as e:

                logger.warning(

                    "Failed to register agent '%s' from config: %s",

                    schema.name,

                    str(e),

                )

        return count



    def load_from_config_dict(self, config):

        # type: (Dict[str, Any]) -> int

        """Load agents from a dictionary and register them.



        Expected format: {"agents": [{...AgentSchema fields...}]}



        Built-in agents are preserved. Config agents are added as CustomAgent

        instances. Duplicate names are skipped with a warning.



        Args:

            config: Dictionary with 'agents' key.



        Returns:

            Number of agents successfully registered.

        """

        from berserker.agent.config import load_agents_from_dict

        from berserker.agent.factory import create_agent_from_schema



        # Load compaction config from the merged config dict

        self._compaction_config = load_compaction_config(config)



        schemas = load_agents_from_dict(config)

        count = 0

        for schema in schemas:

            if agent_registry.has(schema.name):

                logger.warning(

                    "Agent '%s' already registered, skipping config definition",

                    schema.name,

                )

                continue

            try:

                schema.native = False

                base_agent = create_agent_from_schema(schema)

                self.register(base_agent)

                count += 1

            except Exception as e:

                logger.warning(

                    "Failed to register agent '%s' from config: %s",

                    schema.name,

                    str(e),

                )

        return count



    def discover_and_load(self):

        # type: () -> int

        """Discover agents from standard paths and register them.



        Scans standard discovery paths in priority order:

        1. {config_dir}/agents/

        2. {cwd}/.berserker/agents/

        3. {cwd}/agents/



        Built-in agents are preserved. Discovered agents are added as

        CustomAgent instances. Duplicate names are skipped.



        Returns:

            Number of agents successfully registered.

        """

        from berserker.agent.discovery import discover_agents

        from berserker.agent.factory import create_agent_from_schema



        schemas = discover_agents()

        count = 0

        for schema in schemas:

            if agent_registry.has(schema.name):

                logger.debug(

                    "Agent '%s' already registered, skipping discovered definition",

                    schema.name,

                )

                continue

            try:

                schema.native = False

                base_agent = create_agent_from_schema(schema)

                self.register(base_agent)

                count += 1

            except Exception as e:

                logger.warning(

                    "Failed to register discovered agent '%s': %s",

                    schema.name,

                    str(e),

                )

        return count



    def load_from_config(self, config):

        # type: (Dict[str, Any]) -> None

        """Update agent configurations from a configuration dict.



        Expected config format (list-based):

            {

                "agents": [

                    {"name": "build", "model": "gpt-4o", "permission": "full"},

                    {"name": "plan", "model": "claude-3.5-sonnet", "permission": "restricted"},

                    {"name": "reviewer", "mode": "primary", "model": "gpt-4o",

                     "prompt": "You are a code reviewer...", "permission": "restricted"}

                ]

            }



        Updates existing agents and registers new custom agents from config.



        Args:

            config: Configuration dictionary with an "agents" key.

        """

        # Load compaction config from the merged config dict

        self._compaction_config = load_compaction_config(config)



        agents_config = config.get("agents", [])

        if not agents_config:

            return



        if not isinstance(agents_config, list):

            logger.warning(

                "agents config must be a list, got %s. Ignoring.",

                type(agents_config).__name__,

            )

            return



        # Step 1: Find the primary agent's model to use for hidden agents

        primary_model = None  # type: Optional[str]

        for agent_dict in agents_config:

            if agent_dict.get("mode") == "primary" and agent_dict.get("model"):

                primary_model = agent_dict["model"]

                break

        # Fallback: use the first registered primary agent's model

        if primary_model is None:

            for name, agent in self._agents.items():

                if agent.mode == "primary":

                    primary_model = agent.model

                    break



        # Step 2: Auto-assign primary model to hidden agents not in config

        if primary_model is not None:

            hidden_names = {"title", "compaction", "summary"}

            for name in hidden_names:

                if name in self._agents and name not in [a.get("name") for a in agents_config]:

                    existing = self._agents[name]

                    with warnings.catch_warnings():

                        warnings.simplefilter("ignore", DeprecationWarning)

                        new_agent = AgentInfo(

                            name=existing.name,

                            mode=existing.mode,

                            model=primary_model,

                            system_prompt=existing.system_prompt,

                            tools=list(existing.tools),

                            description=existing.description,

                            permission=existing.permission,

                            max_tool_iterations=existing.max_tool_iterations,

                        )

                    self._agents[name] = new_agent

                    logger.info(

                        "Hidden agent '%s' auto-assigned model '%s' from primary agent",

                        name,

                        primary_model,

                    )



        for agent_dict in agents_config:

            agent_name = agent_dict.get("name")

            if not agent_name:

                continue



            if agent_name in self._agents:

                # Update existing agent

                existing = self._agents[agent_name]



                # Handle prompt_append: append to existing system_prompt

                prompt_append = agent_dict.get("prompt_append", "")

                base_prompt = agent_dict.get(

                    "system_prompt", agent_dict.get("prompt", existing.system_prompt)

                )

                if prompt_append and base_prompt == existing.system_prompt:

                    # Only append if base_prompt wasn't explicitly overridden

                    final_prompt = existing.system_prompt + "\n\n" + prompt_append

                else:

                    final_prompt = base_prompt



                # Build new AgentInfo with overrides (internal use, suppress warning)

                with warnings.catch_warnings():

                    warnings.simplefilter("ignore", DeprecationWarning)

                    new_agent = AgentInfo(

                        name=existing.name,

                        mode=existing.mode,

                        model=agent_dict.get("model", existing.model),

                        system_prompt=final_prompt,

                        tools=agent_dict.get("tools", existing.tools),

                        description=agent_dict.get("description", existing.description),

                        permission=agent_dict.get("permission", existing.permission),

                        max_tool_iterations=agent_dict.get(

                            "max_tool_iterations", existing.max_tool_iterations

                        ),

                    )

                self._agents[agent_name] = new_agent

                # Keep the BaseAgent in agent_registry in sync so the

                # schema-driven API does not drift from _agents.

                if agent_registry.has(agent_name):

                    _reg_schema = agent_registry.get(agent_name)._schema

                    _reg_schema.model = new_agent.model

                    _reg_schema.prompt = new_agent.system_prompt

                    _reg_schema.options["tools"] = list(new_agent.tools)

                    _reg_schema.permission = new_agent.permission

            else:

                # Register new custom agent from config

                prompt = agent_dict.get("prompt") or agent_dict.get("system_prompt", "")

                if not prompt:

                    logger.warning(

                        "Skipping custom agent '%s': no 'prompt' or 'system_prompt' provided",

                        agent_name,

                    )

                    continue



                mode = agent_dict.get("mode", "primary")

                model = agent_dict.get("model", "")

                if not model:

                    logger.warning(

                        "Skipping custom agent '%s': no 'model' specified",

                        agent_name,

                    )

                    continue



                tools = agent_dict.get("tools", [])

                description = agent_dict.get("description", "")

                permission = agent_dict.get("permission", "full")

                max_tool_iterations = agent_dict.get(

                    "max_tool_iterations", _DEFAULT_MAX_TOOL_ITERATIONS

                )

                options = agent_dict.get("options", {})

                if not max_tool_iterations and options.get("max_tool_iterations"):

                    max_tool_iterations = options["max_tool_iterations"]

                # Also check options.tools when top-level tools is empty

                if not tools and options.get("tools"):

                    tools = options["tools"]



                # Build AgentInfo for custom agent (internal use, suppress warning)

                with warnings.catch_warnings():

                    warnings.simplefilter("ignore", DeprecationWarning)

                    custom_agent = AgentInfo(

                        name=agent_name,

                        mode=mode,

                        model=model,

                        system_prompt=prompt,

                        tools=tools,

                        description=description,

                        permission=permission,

                        max_tool_iterations=max_tool_iterations,

                    )

                self._agents[agent_name] = custom_agent

                logger.info(

                    "Registered custom agent '%s' (mode=%s, model=%s)",

                    agent_name,

                    mode,

                    model,

                )



    def register(self, agent):

        # type: (Any) -> None

        """Register an agent dynamically.



        Accepts either a BaseAgent instance (new API) or an AgentInfo

        instance (legacy API). Registers into both the global AgentRegistry

        and the internal _agents dict for backward-compatible execute().



        Args:

            agent: A BaseAgent or AgentInfo instance to register.



        Raises:

            TypeError: If agent is not a BaseAgent or AgentInfo instance.

            AgentRegistrationError: If an agent with the same name exists.

        """

        from berserker.agent.base import BaseAgent

        from berserker.agent.exceptions import AgentRegistrationError



        if isinstance(agent, BaseAgent):

            # New API: BaseAgent instance

            # Register in global registry (thread-safe)

            agent_registry.register(agent)



            # Also create AgentInfo for backward-compatible execute() path (internal use)

            with warnings.catch_warnings():

                warnings.simplefilter("ignore", DeprecationWarning)

                agent_info = AgentInfo(

                    name=agent.name,

                    mode=agent.mode,

                    model=agent.model,

                    system_prompt=agent.system_prompt,

                    tools=agent.tools,

                    description=agent.description,

                    permission=agent.permission,

                    max_tool_iterations=agent.max_tool_iterations,

                )

            self._agents[agent.name] = agent_info

            self._base_agents[agent.name] = agent

            logger.info("Registered agent '%s' (mode=%s)", agent.name, agent.mode)



        elif isinstance(agent, AgentInfo):

            # Legacy API: AgentInfo instance

            if agent.name in self._agents:

                raise AgentRegistrationError(

                    agent.name,

                    "Agent '{}' already exists. Use a different name.".format(agent.name),

                )

            self._agents[agent.name] = agent

            logger.info("Registered legacy agent '%s'", agent.name)



        else:

            raise TypeError("Expected BaseAgent or AgentInfo, got {}".format(type(agent).__name__))



    def unregister(self, name):

        # type: (str) -> None

        """Remove an agent by name from both registry and internal storage.



        Args:

            name: The name of the agent to remove.



        Raises:

            AgentNotFoundError: If no agent with the given name exists.

        """

        from berserker.agent.exceptions import AgentNotFoundError



        # Remove from internal storage first

        if name not in self._agents:

            raise AgentNotFoundError(name)

        del self._agents[name]

        self._base_agents.pop(name, None)



        # Also unregister from global registry (thread-safe)

        # Only if it was a BaseAgent-registered agent

        if agent_registry.has(name):

            agent_registry.unregister(name)



        logger.info("Unregistered agent '%s'", name)



    def generate_agent(self, description, provider=None, model=None):

        # type: (str, Optional[str], Optional[str]) -> Any

        """Generate an agent configuration from a natural language description.



        Uses an LLM provider to convert the description into a validated

        AgentSchema, then creates and returns a BaseAgent instance.

        The agent is NOT automatically registered.



        Args:

            description: Natural language description of the desired agent.

            provider: Optional provider ID to use for generation.

            model: Optional model name to use for generation.



        Returns:

            A BaseAgent instance (BuiltInAgent or CustomAgent).



        Raises:

            AgentGenerationError: If generation fails.

        """

        from berserker.agent.generator import AgentGenerator



        generator = AgentGenerator(agent_registry, provider_registry)

        schema = generator.generate(description, provider, model)

        return create_agent_from_schema(schema)



    def generate_and_register(self, description, provider=None, model=None):

        # type: (str, Optional[str], Optional[str]) -> str

        """Generate an agent from a description and register it.



        Uses an LLM provider to convert the description into a validated

        AgentSchema, creates the agent, and registers it in both the

        global AgentRegistry and the internal AgentManager storage.



        Args:

            description: Natural language description of the desired agent.

            provider: Optional provider ID to use for generation.

            model: Optional model name to use for generation.



        Returns:

            The name of the registered agent.



        Raises:

            AgentGenerationError: If generation or registration fails.

        """

        from berserker.agent.generator import AgentGenerator



        generator = AgentGenerator(agent_registry, provider_registry)

        agent_name = generator.generate_and_register(description, provider, model)



        # Also register in internal _agents dict for backward-compatible execute()

        base_agent = agent_registry.get(agent_name)

        with warnings.catch_warnings():

            warnings.simplefilter("ignore", DeprecationWarning)

            agent_info = AgentInfo(

                name=base_agent.name,

                mode=base_agent.mode,

                model=base_agent.model,

                system_prompt=base_agent.system_prompt,

                tools=base_agent.tools,

                description=base_agent.description,

                permission=base_agent.permission,

                max_tool_iterations=base_agent.max_tool_iterations,

            )

        self._agents[agent_name] = agent_info

        self._base_agents[agent_name] = base_agent



        return agent_name



    def send_message(

        self,

        to_agent,  # type: str

        content,  # type: str

        from_agent=None,  # type: Optional[str]

        session_id=None,  # type: Optional[str]

        metadata=None,  # type: Optional[Dict[str, Any]]

    ):

        # type: (...) -> AgentMessage

        """Send a message to another agent via the message router.



        Automatically registers both sender and recipient agents if not

        already registered. Publishes AGENT_MESSAGE_SENT event on success

        or AGENT_MESSAGE_FAILED on error.



        Args:

            to_agent: Name of the receiving agent.

            content: Message content string.

            from_agent: Name of the sending agent (default: None).

            session_id: Associated session identifier (default: None).

            metadata: Additional key-value data (default: None).



        Returns:

            The created AgentMessage.



        Raises:

            ValueError: If to_agent is empty.

        """

        if not to_agent:

            raise ValueError("to_agent cannot be empty")



        # Ensure agents are registered

        message_router.register_agent(to_agent)

        if from_agent:

            message_router.register_agent(from_agent)



        msg = AgentMessage(

            from_agent=from_agent or "unknown",

            to_agent=to_agent,

            session_id=session_id or "",

            content=content,

            metadata=metadata,

        )



        try:

            message_router.send(msg)

            bus.publish(

                AGENT_MESSAGE_SENT,

                {

                    "message_id": msg.message_id,

                    "from_agent": msg.from_agent,

                    "to_agent": msg.to_agent,

                    "session_id": msg.session_id,

                },

            )

            logger.debug(

                "Message sent: %s -> %s (id=%s)",

                msg.from_agent,

                msg.to_agent,

                msg.message_id[:8],

            )

        except Exception as exc:

            msg.status = "failed"

            bus.publish(

                AGENT_MESSAGE_FAILED,

                {

                    "message_id": msg.message_id,

                    "from_agent": msg.from_agent,

                    "to_agent": msg.to_agent,

                    "error": str(exc),

                },

            )

            logger.warning(

                "Message failed: %s -> %s (id=%s): %s",

                msg.from_agent,

                msg.to_agent,

                msg.message_id[:8],

                exc,

            )

            raise



        return msg



    def receive_messages(self, agent_name):

        # type: (str) -> List[AgentMessage]

        """Receive all pending messages for an agent.



        Args:

            agent_name: Name of the receiving agent.



        Returns:

            List of AgentMessage objects (empty if none pending).

        """

        messages = message_router.receive(agent_name)



        if messages:

            bus.publish(

                AGENT_MESSAGE_RECEIVED,

                {

                    "agent_name": agent_name,

                    "count": len(messages),

                    "message_ids": [m.message_id for m in messages],

                },

            )

            logger.debug("Received %d message(s) for agent '%s'", len(messages), agent_name)



        return messages



    # ---------------------------------------------------------------------------

    # Orchestration Methods (Phase 6)

    # ---------------------------------------------------------------------------



    def execute_parallel(self, tasks):

        # type: (List[Any]) -> List[Dict[str, Any]]

        """Execute multiple tasks concurrently using the orchestrator.



        Convenience method that delegates to Orchestrator.execute_parallel().



        Args:

            tasks: List of OrchestrationTask objects to execute.



        Returns:

            List of result dicts, one per task (in original order).

        """

        from berserker.agent.orchestrator import get_orchestrator



        orchestrator = get_orchestrator(self, tool_registry)

        return orchestrator.execute_parallel(tasks)



    def execute_sequential(self, tasks):

        # type: (List[Any]) -> List[Dict[str, Any]]

        """Execute tasks sequentially using the orchestrator.



        Convenience method that delegates to Orchestrator.execute_sequential().



        Args:

            tasks: List of OrchestrationTask objects to execute.



        Returns:

            List of result dicts, in execution order.

        """

        from berserker.agent.orchestrator import get_orchestrator



        orchestrator = get_orchestrator(self, tool_registry)

        return orchestrator.execute_sequential(tasks)



    def execute_fan_out_fan_in(

        self,

        fan_out_tasks,  # type: List[Any]

        fan_in_agent,  # type: str

        fan_in_prompt,  # type: str

        fan_in_session_id=None,  # type: Optional[str]

    ):

        # type: (...) -> Dict[str, Any]

        """Execute fan-out tasks in parallel, then aggregate with a fan-in agent.



        Convenience method that delegates to Orchestrator.execute_fan_out_fan_in().



        Args:

            fan_out_tasks: List of OrchestrationTask objects for fan-out phase.

            fan_in_agent: Agent name for the fan-in aggregation step.

            fan_in_prompt: Prompt for the fan-in agent.

            fan_in_session_id: Optional session ID for fan-in execution.



        Returns:

            Dict with 'fan_out_results' and 'fan_in_result'.

        """

        from berserker.agent.orchestrator import get_orchestrator



        orchestrator = get_orchestrator(self, tool_registry)

        return orchestrator.execute_fan_out_fan_in(

            fan_out_tasks, fan_in_agent, fan_in_prompt, fan_in_session_id

        )





# ---------------------------------------------------------------------------

# Singleton Instance

# ---------------------------------------------------------------------------



agent_manager = AgentManager()

