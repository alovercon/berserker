# -*- coding: utf-8 -*-
"""Tests for large-artifact persistence in tool/task.py (_persist_large_artifact)."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from berserker.tool.task import _persist_large_artifact  # noqa: E402


class TestPersistLargeArtifact(object):
    def test_small_content_inline_unchanged(self, tmp_path):
        # Small content is returned as-is (no disk write).
        small = "short result"
        out = _persist_large_artifact(small, "sess-abc", "critic")
        assert out == small

    def test_large_content_persisted_with_path_reference(self, tmp_path, monkeypatch):
        # Force workspace to tmp_path
        monkeypatch.setattr(
            "berserker.workspace.get_workspace", lambda: str(tmp_path)
        )
        # >8192 bytes triggers persistence
        large = "x" * 9000
        out = _persist_large_artifact(large, "sess-abc", "critic")

        # Output must contain the path reference
        assert "完整产物已落盘" in out
        assert "请直接引用该文件路径" in out
        assert "agent_outputs" in out
        # File must exist under tmp_path/.berserker/agent_outputs/
        assert os.path.isdir(str(tmp_path / ".berserker" / "agent_outputs"))
        files = os.listdir(str(tmp_path / ".berserker" / "agent_outputs"))
        assert len(files) == 1
        # The file contains the original content
        with open(str(tmp_path / ".berserker" / "agent_outputs" / files[0]),
                  encoding="utf-8") as f:
            assert f.read() == large

    def test_failure_falls_back_to_original(self, tmp_path, monkeypatch):
        # Make workspace resolution raise → fallback to original content
        def boom():
            raise RuntimeError("no workspace")
        monkeypatch.setattr("berserker.workspace.get_workspace", boom)
        large = "y" * 9000
        out = _persist_large_artifact(large, "sess-abc", "critic")
        assert out == large  # no crash, original returned

    def test_threshold_boundary(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "berserker.workspace.get_workspace", lambda: str(tmp_path)
        )
        # Exactly 8192 bytes → NOT persisted (inline threshold is inclusive)
        exact = "z" * 8192
        out = _persist_large_artifact(exact, "s", "critic")
        assert out == exact
