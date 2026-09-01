# -*- coding: utf-8 -*-
"""Tests for stuck-loop sliding-window detection in agent/executor.py."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from berserker.agent.executor import _stuck_loop_detected, _tool_call_signature  # noqa: E402


def _tc(name, args):
    """Build a tool_call dict."""
    return {"id": "x", "type": "function",
            "function": {"name": name, "arguments": args}}


class TestToolCallSignature(object):
    def test_stable_for_same_call(self):
        a = _tool_call_signature([_tc("read", '{"path": "x.py"}')])
        b = _tool_call_signature([_tc("read", '{"path": "x.py"}')])
        assert a == b

    def test_normalized_argument_order(self):
        a = _tool_call_signature([_tc("edit", '{"path":"f","a":1,"b":2}')])
        b = _tool_call_signature([_tc("edit", '{"b":2,"path":"f","a":1}')])
        assert a == b

    def test_different_args_differ(self):
        a = _tool_call_signature([_tc("read", '{"path": "x.py"}')])
        b = _tool_call_signature([_tc("read", '{"path": "y.py"}')])
        assert a != b

    def test_order_independent_multi_calls(self):
        a = _tool_call_signature([_tc("a", "{}"), _tc("b", "{}")])
        b = _tool_call_signature([_tc("b", "{}"), _tc("a", "{}")])
        assert a == b

    def test_unparseable_args_fall_back(self):
        # Raw string that is not JSON must not crash and must still hash.
        a = _tool_call_signature([_tc("bash", "not json")])
        b = _tool_call_signature([_tc("bash", "not json")])
        assert a == b


class TestStuckLoopDetected(object):
    def test_no_loop_when_varied(self):
        w = []
        # Varied recon calls — must NEVER trigger
        for i in range(20):
            detected = _stuck_loop_detected(w, [_tc("read", '{"p": %d}' % i)], 6, 3)
            assert detected is False

    def test_aaaa_loop_triggers(self):
        w = []
        detected = False
        for _ in range(6):
            detected = _stuck_loop_detected(w, [_tc("read", '{"p":"x"}')], 6, 3)
        assert detected is True

    def test_abab_loop_triggers(self):
        w = []
        detected = False
        calls = [_tc("a", "{}"), _tc("b", "{}")]
        for i in range(8):
            detected = _stuck_loop_detected(w, [calls[i % 2]], 6, 3)
        # After 8 alternations, 'a' appears 4 times and 'b' 4 times in window — both >= 3
        assert detected is True

    def test_below_threshold_no_trigger(self):
        w = []
        # Only 2 repeats — below threshold 3
        detected = _stuck_loop_detected(w, [_tc("read", "{}")], 6, 3)
        detected = _stuck_loop_detected(w, [_tc("read", "{}")], 6, 3)
        assert detected is False

    def test_window_slides_off_old_signatures(self):
        w = []
        # 3 repeats would trigger, but if they fall out of the 6-size window
        # before reaching threshold pattern, no trigger.
        for _ in range(6):
            _stuck_loop_detected(w, [_tc("a", "{}")], 6, 3)  # fill window with 'a'
        # Now push 6 varied calls to slide 'a' out
        detected = False
        for i in range(6):
            detected = _stuck_loop_detected(w, [_tc("varied", '{"i": %d}' % i)], 6, 3)
        assert detected is False
