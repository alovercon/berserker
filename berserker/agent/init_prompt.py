"""Shared prompt template for AGENTS.md generation (/init command).

Used by both CLI (conversation.py) and GUI (app.py) to ensure consistent
AGENTS.md generation behavior.

Python 3.8.10 compatible.
"""

from __future__ import annotations

import os
from typing import Optional

_INIT_PROMPT_TEMPLATE = """Create or update `AGENTS.md` for this repository.

The goal is a compact instruction file that helps future AI coding sessions avoid mistakes and ramp up quickly. Every line should answer: "Would an agent likely miss this without help?" If not, leave it out.

User-provided focus or constraints (honor these):
{user_focus}

## How to investigate

{investigation_section}

If the architecture is still unclear after reading these, inspect a small number of representative code files to find the real entrypoints, package boundaries, and execution flow. Prefer reading the files that explain how the system is wired together over random leaf files.

Prefer executable sources of truth over prose. If docs conflict with config or scripts, trust the executable source and only keep what you can verify.

## What to extract

Look for the highest-signal facts for an agent working in this repo:
- exact developer commands, especially non-obvious ones
- how to run a single test, a single package, or a focused verification step
- required command order when it matters, such as `lint -> typecheck -> test`
- monorepo or multi-package boundaries, ownership of major directories, and the real app/library entrypoints
- framework or toolchain quirks: generated code, migrations, codegen, build artifacts, special env loading, dev servers, infra deploy flow
- repo-specific style or workflow conventions that differ from defaults
- testing quirks: fixtures, integration test prerequisites, snapshot workflows, required services, flaky or expensive suites
- important constraints from existing instruction files worth preserving

Good `AGENTS.md` content is usually hard-earned context that took reading multiple files to infer.

## Questions

Only ask the user questions if the repo cannot answer something important. Use the `question` tool for one short batch at most.

Good questions:
- undocumented team conventions
- branch / PR / release expectations
- missing setup or test prerequisites that are known but not written down

Do not ask about anything the repo already makes clear.

## Writing rules

Include only high-signal, repo-specific guidance such as:
- exact commands and shortcuts the agent would otherwise guess wrong
- architecture notes that are not obvious from filenames
- conventions that differ from language or framework defaults
- setup requirements, environment quirks, and operational gotchas
- references to existing instruction sources that matter

Exclude:
- generic software advice
- long tutorials or exhaustive file trees
- obvious language conventions
- speculative claims or anything you could not verify
- content better stored in another file referenced via config `instructions`

When in doubt, omit.

Prefer short sections and bullets. If the repo is simple, keep the file simple. If the repo is large, summarize the few structural facts that actually change how an agent should work.

{existing_note}

Return ONLY the content for AGENTS.md. Do not include any explanation or preamble.
"""


def _build_investigation_section(analysis_text=None):
    # type: (Optional[str]) -> str
    """Build the investigation section based on whether pre-scanned content is available."""
    if analysis_text and analysis_text.strip():
        return (
            "I have pre-scanned some key configuration files in the workspace. "
            "Use them as a starting point:\n\n"
            "{}".format(analysis_text)
        )
    else:
        return (
            "Read the highest-value sources first:\n"
            "- `README*`, root manifests, workspace config, lockfiles\n"
            "- build, test, lint, formatter, typecheck, and codegen config\n"
            "- CI workflows and pre-commit / task runner config\n"
            "- existing instruction files (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/`, "
            "`.cursorrules`, `.github/copilot-instructions.md`)\n"
            "- repo-local config files"
        )


def _build_existing_note(agents_md_path, existing_content=None):
    # type: (str, Optional[str]) -> str
    """Build the existing file note section."""
    if existing_content and existing_content.strip():
        return (
            "If `AGENTS.md` already exists at `{}`, improve it in place rather than "
            "rewriting blindly. Preserve verified useful guidance, delete fluff or stale claims, "
            "and reconcile it with the current codebase. Here is the current content:\n\n"
            "=== Current AGENTS.md ===\n"
            "{}"
        ).format(agents_md_path, existing_content)
    else:
        return "AGENTS.md does not exist yet. Create it from scratch."


def build_init_prompt(
    user_focus,
    analysis_text=None,
    existing_agents_md=None,
    agents_md_path=None,
):
    # type: (str, Optional[str], Optional[str], Optional[str]) -> str
    """Build the complete /init prompt for AGENTS.md generation.

    Args:
        user_focus: User-provided focus or constraints (e.g. "focus on testing").
        analysis_text: Optional pre-scanned file content (CLI provides this, GUI passes None).
        existing_agents_md: Optional existing AGENTS.md content to improve.
        agents_md_path: Path to existing AGENTS.md file (used in the prompt for clarity).

    Returns:
        Complete prompt string ready to send to the LLM.
    """
    if not user_focus or not user_focus.strip():
        user_focus = "(none specified)"

    investigation_section = _build_investigation_section(analysis_text)

    if agents_md_path is None:
        agents_md_path = "AGENTS.md"

    existing_note = _build_existing_note(agents_md_path, existing_agents_md)

    return _INIT_PROMPT_TEMPLATE.format(
        user_focus=user_focus,
        investigation_section=investigation_section,
        existing_note=existing_note,
    )


def load_existing_agents_md(workspace):
    # type: (str) -> Optional[str]
    """Load existing AGENTS.md content from workspace, if present.

    Args:
        workspace: Workspace directory path.

    Returns:
        File content string, or None if file doesn't exist or can't be read.
    """
    agents_md_path = os.path.join(workspace, "AGENTS.md")
    if not os.path.isfile(agents_md_path):
        return None
    try:
        with open(agents_md_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None
