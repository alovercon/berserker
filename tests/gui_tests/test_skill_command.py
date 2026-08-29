# -*- coding: utf-8 -*-
"""Regression tests: bare /<skill-name> must be recognized and loaded."""

import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from berserker.gui.controller import SlashCommandHandler  # noqa: E402


@pytest.fixture
def handler():
    from berserker.command.registry import command_registry

    # The GUI calls command_registry.scan() at startup; replicate it here so
    # builtin commands (help, agent, ...) are registered in tests.
    command_registry.scan(_REPO_ROOT)

    class Dummy(object):
        session_id = "s1"

    return SlashCommandHandler(Dummy())


class TestSkillCommandRecognition(object):
    def test_skill_command_recognized(self, handler):
        is_cmd, result = handler.process("/skill-creator")
        assert is_cmd is True
        assert result.startswith("__SKILL__:")
        assert len(result) > len("__SKILL__:")

    def test_unknown_command_still_unknown(self, handler):
        is_cmd, result = handler.process("/zzz-not-a-real-command")
        assert is_cmd is True
        assert result.startswith("Unknown command")

    def test_help_command_unaffected(self, handler):
        is_cmd, result = handler.process("/help")
        assert is_cmd is True
        assert "Available commands" in result

    def test_skill_load_returns_markdown(self):
        # Direct: _find_skill/_load_skill must locate an installed skill.
        from berserker.tool.skill import _find_skill, _load_skill, _list_available_skills

        names = [s.get("name") for s in _list_available_skills()]
        if not names:
            pytest.skip("no skills installed in this environment")
        found = _find_skill(names[0])
        assert found is not None
        content = _load_skill(found)
        assert len(content) > 0
