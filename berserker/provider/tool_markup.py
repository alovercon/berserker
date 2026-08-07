"""
berserker.provider.tool_markup — recover tool-call markup leaked into content.

Some models natively serialize tool calls as text markup. API servers
normally convert that to structured ``tool_calls`` when the request declares
tools, but in some conditions (long context, malformed/missing wrapper
tokens, a proxy/gateway in between) the raw markup leaks into the assistant
``content`` as plain text. This module detects and parses such leaked markup
back into the unified tool_calls format.

Supported formats
-----------------

DeepSeek DSML (canonical grammar, vLLM deepseek_v4 parser)::

    <｜DSML｜tool_calls>
    <｜DSML｜invoke name="func_name">
    <｜DSML｜parameter name="location" string="true">杭州</｜DSML｜parameter>
    <｜DSML｜parameter name="count" string="false">5</｜DSML｜parameter>
    </｜DSML｜invoke>
    </｜DSML｜tool_calls>

- ``string="true"``  → value used as a plain string
- ``string="false"`` → value parsed as JSON (number/bool/…)
- tolerated deviations: doubled full-width pipes (``｜｜DSML｜｜``), a missing
  outer ``tool_calls`` wrapper (vllm#48931), prose around the markup

Qwen XML (both shapes seen in the wild)::

    <tool_call><name>ls</name><parameters>{"path":"."}</parameters></tool_call>

    <tool_call>{"name": "ls", "arguments": {"path": "."}}</tool_call>

- optionally wrapped in ```xml markdown fences
- multiple ``tool_call`` blocks allowed

Python 3.8.10 compatible.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Full-width pipe used by DSML markers (U+FF5C).
_P = "｜"

# Cheap pre-check markers for any supported markup family.
_MARKERS = ("DSML", "<invoke", "<tool_call")

# ── DSML ─────────────────────────────────────────────────────────────

_INVOKE_RE = re.compile(
    r"<" + _P + r"DSML" + _P + r"invoke\s+name=\"(?P<name>[^\"]+)\"\s*" + _P + r"?>"
    r"(?P<body>.*?)"
    r"</" + _P + r"DSML" + _P + r"invoke>",
    re.DOTALL,
)

_PARAM_RE = re.compile(
    r"<" + _P + r"DSML" + _P + r"parameter\s+name=\"(?P<name>[^\"]+)\""
    r"(?:\s+string=\"(?P<string>true|false)\")?\s*" + _P + r"?>"
    r"(?P<value>.*?)"
    r"</" + _P + r"DSML" + _P + r"parameter>",
    re.DOTALL,
)

# ── Qwen XML ─────────────────────────────────────────────────────────

_QWEN_BLOCK_RE = re.compile(
    r"<tool_call>\s*(?P<body>.*?)\s*</tool_call>",
    re.DOTALL,
)

_QWEN_NAME_RE = re.compile(r"<name>\s*(?P<name>[^<]+?)\s*</name>", re.DOTALL)
_QWEN_PARAMS_RE = re.compile(r"<parameters>\s*(?P<params>.*?)\s*</parameters>", re.DOTALL)

_QWEN_FENCE_RE = re.compile(r"```(?:xml|json)?\s*\r?\n?")


def contains_tool_markup(text):
    # type: (str) -> bool
    """Cheap pre-check: does the text look like it carries tool-call markup?"""
    if not text:
        return False
    return any(m in text for m in _MARKERS)


def _parse_dsml_value(raw, is_string):
    # type: (str, bool) -> Any
    """Convert a DSML parameter value per its ``string=`` annotation."""
    value = raw.strip()
    if is_string:
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        # Malformed JSON in a string="false" parameter — keep the raw text
        # rather than dropping the argument entirely.
        return value


def _make_call(index, name, args):
    # type: (int, str, Dict[str, Any]) -> Dict[str, Any]
    return {
        "id": "call_markup_{}".format(index),
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def _parse_dsml(text, tool_calls, spans):
    # type: (str, List[Dict[str, Any]], List[Tuple[int, int]]) -> None
    """Collect DSML invokes into tool_calls/spans (mutates both lists)."""
    for m in _INVOKE_RE.finditer(text):
        name = m.group("name").strip()
        if not name:
            continue
        args = {}  # type: Dict[str, Any]
        for pm in _PARAM_RE.finditer(m.group("body")):
            pname = pm.group("name").strip()
            is_string = (pm.group("string") or "true").lower() != "false"
            args[pname] = _parse_dsml_value(pm.group("value"), is_string)
        tool_calls.append(_make_call(len(tool_calls), name, args))
        spans.append(m.span())


def _parse_qwen(text, tool_calls, spans):
    # type: (str, List[Dict[str, Any]], List[Tuple[int, int]]) -> None
    """Collect Qwen XML tool_call blocks (mutates tool_calls/spans)."""
    for m in _QWEN_BLOCK_RE.finditer(text):
        body = m.group("body")
        name = ""
        args = {}  # type: Dict[str, Any]

        nm = _QWEN_NAME_RE.search(body)
        if nm:
            # Shape 1: <name>..</name><parameters>{..}</parameters>
            name = nm.group("name").strip()
            pm = _QWEN_PARAMS_RE.search(body)
            if pm:
                try:
                    parsed = json.loads(pm.group("params"))
                    if isinstance(parsed, dict):
                        args = parsed
                except (ValueError, TypeError):
                    pass
        else:
            # Shape 2: raw JSON object inside <tool_call>
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    name = str(parsed.get("name") or "").strip()
                    raw_args = parsed.get("arguments") or parsed.get("parameters") or {}
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except (ValueError, TypeError):
                            raw_args = {}
                    if isinstance(raw_args, dict):
                        args = raw_args
            except (ValueError, TypeError):
                continue

        if not name:
            continue
        tool_calls.append(_make_call(len(tool_calls), name, args))
        spans.append(m.span())


def parse_tool_markup(text):
    # type: (str) -> Tuple[str, Optional[List[Dict[str, Any]]]]
    """Extract leaked tool-call markup from assistant content.

    Args:
        text: Assistant message content potentially containing markup.

    Returns:
        ``(clean_content, tool_calls)`` — content with all markup regions
        (and any wrapping markdown fences) removed, and the recovered calls
        in the unified OpenAI-ish format (``None`` when nothing parseable
        was found).
    """
    if not contains_tool_markup(text):
        return text, None

    tool_calls = []  # type: List[Dict[str, Any]]
    spans = []  # type: List[Tuple[int, int]]

    # Normalise once (doubled-pipe DSML variant) so spans from both parsers
    # share the same coordinate space.
    norm = text.replace(_P * 2, _P)
    _parse_dsml(norm, tool_calls, spans)
    _parse_qwen(norm, tool_calls, spans)

    if not tool_calls:
        return text, None

    # Remove markup regions from the content.
    cleaned = norm
    for start, end in sorted(spans, reverse=True):
        cleaned = cleaned[:start] + cleaned[end:]
    for marker in (
        "<" + _P + "DSML" + _P + "tool_calls>",
        "</" + _P + "DSML" + _P + "tool_calls>",
    ):
        cleaned = cleaned.replace(marker, "")
    # Strip markdown fences left around removed blocks and blank runs
    cleaned = _QWEN_FENCE_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    # A fence closer without an opener may remain
    cleaned = cleaned.replace("```", "").strip()

    logger.warning(
        "[tool_markup] Recovered %d tool call(s) from leaked markup: %s",
        len(tool_calls),
        [tc["function"]["name"] for tc in tool_calls],
    )
    return cleaned, tool_calls
