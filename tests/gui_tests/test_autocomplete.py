# -*- coding: utf-8 -*-
"""Tests for berserker.gui.autocomplete (pure logic, no wx dependency)."""

import os
import sys

import pytest

# Ensure the repo root is importable
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from berserker.gui.autocomplete import (  # noqa: E402
    Suggestion,
    get_candidates,
    get_query_from_text,
)


# ---------------------------------------------------------------------------
# get_query_from_text
# ---------------------------------------------------------------------------


class TestGetQueryFromText(object):
    def test_bare_slash_returns_empty(self):
        # "/" -> query is "" (show all candidates)
        assert get_query_from_text("/") == ""

    def test_slash_plus_query(self):
        assert get_query_from_text("/help") == "help"
        assert get_query_from_text("/comp") == "comp"

    def test_not_in_slash_command(self):
        assert get_query_from_text("hello world") is None

    def test_slash_in_middle_of_sentence(self):
        # Cursor positioned right after "bad" (word starts with '/')
        text = "fix /bad thing"
        assert get_query_from_text(text, cursor_pos=8) == "bad"

    def test_cursor_position_inside_word(self):
        # Cursor after "hel": still in the word
        assert get_query_from_text("/help", cursor_pos=4) == "hel"

    def test_cursor_after_space_not_command(self):
        # "/help " with cursor after space -> not in command
        assert get_query_from_text("/help ", cursor_pos=6) is None

    def test_cursor_before_slash_start(self):
        # "abc /xyz" with cursor at position 4 (right before '/'):
        # the token containing the cursor starts with '/', so the query is ""
        # (show all candidates). Cursor sits on the token boundary — showing
        # the full list is the friendlier behavior.
        assert get_query_from_text("abc /xyz", cursor_pos=4) == ""


# ---------------------------------------------------------------------------
# get_candidates
# ---------------------------------------------------------------------------


class TestGetCandidates(object):
    @pytest.fixture(autouse=True)
    def _scan_commands(self):
        """Ensure the command registry has built-in commands registered."""
        from berserker.command.registry import command_registry
        command_registry.scan(_REPO_ROOT)
        yield

    def test_empty_query_returns_candidates(self):
        cands = get_candidates("")
        # Every candidate must be a Suggestion with a label
        assert all(isinstance(c, Suggestion) for c in cands)
        # Commands listed before skills
        kinds = [c.kind for c in cands]
        first_skill = next((i for i, k in enumerate(kinds) if k == "skill"), len(kinds))
        assert all(k == "command" for k in kinds[:first_skill])

    def test_prefix_filter_commands(self):
        cands = get_candidates("comp")
        assert any(c.label == "/compact" for c in cands)
        assert all("/comp" in c.label for c in cands)

    def test_prefix_filter_skills(self):
        # "skill" prefix matches both the /skills command and any installed
        # skill named like "skill-*". At least the /skills command must appear;
        # installed-skill results are environment-dependent.
        cands = get_candidates("skill")
        assert any(c.label == "/skills" for c in cands)

    def test_no_match_returns_empty(self):
        assert get_candidates("zzz-no-such-command") == []

    def test_deduplication(self):
        cands = get_candidates("")
        labels = [c.label for c in cands]
        assert len(labels) == len(set(labels))

    def test_max_cap(self):
        cands = get_candidates("")
        from berserker.gui.autocomplete import MAX_SUGGESTIONS
        assert len(cands) <= MAX_SUGGESTIONS


# ---------------------------------------------------------------------------
# Suggestion serialization
# ---------------------------------------------------------------------------


class TestSuggestion(object):
    def test_as_dict(self):
        s = Suggestion(label="/help", display="/help", kind="command",
                       description="Show help")
        d = s.as_dict()
        assert d["label"] == "/help"
        assert d["kind"] == "command"
        assert d["description"] == "Show help"
