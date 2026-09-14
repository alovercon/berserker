"""Compaction gate must agree with the trigger's token counter, and the
context-retry path must never resend an un-shrunk over-limit payload.

Regression tests for the DeepSeek-400 incident: the trigger fired
(count_messages ~836k > threshold) while compact()'s internal gate
(naive chars/4, ~733k) concluded "under budget" and returned the full
history unchanged -- so a byte-identical over-limit payload was sent twice
and hard-failed. Ported from the embedded-ai fork (tests/test_compaction_gate.py).
Python 3.8.10 compatible.
"""

import json

from berserker.agent.compaction import CompactionConfig
from berserker.agent.manager import AgentManager, _msg_token_count
from berserker.provider.base import ChatMessage
from berserker.session.token_counter import TokenCounter


def _make_manager():
    """Bare AgentManager instance: compact() only needs _compaction_config;
    the AI-summarization path (self.execute) fails fast on the uninitialized
    instance and compact() falls back to the placeholder summary."""
    mgr = AgentManager.__new__(AgentManager)
    mgr._compaction_config = CompactionConfig()
    return mgr


def _tiny_asst(i):
    """Tiny assistant message: the fixed per-message overhead in count_messages
    (+3 message, +3 assistant) has no counterpart in naive chars/4, so the two
    estimators diverge deterministically regardless of language detection."""
    return ChatMessage(
        role="assistant",
        content="ok",
        reasoning_content="done",
    )


def _history(turns=300):
    msgs = [ChatMessage(role="system", content="sys")]
    for i in range(turns):
        msgs.append(ChatMessage(role="user", content="q%d" % i))
        msgs.append(_tiny_asst(i))
    return msgs


def test_msg_token_count_matches_trigger_counter():
    """The compaction gate must count through the SAME estimator the trigger
    uses (count_messages: overhead + language-aware), not naive chars/4."""
    msg = _tiny_asst(1)
    assert _msg_token_count(msg) == TokenCounter().count_messages([msg], "gpt-4o")


def test_compaction_actually_compacts_over_budget():
    """chars/4 < max_tokens < count_messages: old gate returns the history
    unchanged (bug); unified gate must actually compact."""
    mgr = _make_manager()
    msgs = _history()
    max_tokens = 2200

    counter = TokenCounter()
    # Fixture must straddle the two estimators.
    content_chars = sum(
        len(m.content or "")
        + len(json.dumps(m.tool_calls or [], ensure_ascii=False))
        + len(m.reasoning_content or "")
        for m in msgs
    )
    assert content_chars // 4 < max_tokens
    assert counter.count_messages(msgs, "gpt-4o") > max_tokens

    result = mgr.compact(msgs, max_tokens=max_tokens)

    assert len(result) < len(msgs)
    assert counter.count_messages(result, "gpt-4o") <= max_tokens


def test_compaction_budget_shrinks_without_exact_tokenizer():
    """Fallback estimation undercounts DeepSeek-class tokenizers by ~30%;
    the budget must carry a safety factor when no exact tokenizer exists."""
    from berserker.agent.executor import compaction_budget

    # tiktoken is absent in this environment -> factor applies.
    assert compaction_budget(1000000, 20000, "deepseek-flash", TokenCounter()) == int(980000 * 0.7)


def test_compaction_budget_full_for_qwen_bpe():
    """Qwen models have the bundled pure-Python BPE tokenizer -> exact, no factor."""
    from berserker.agent.executor import compaction_budget

    assert compaction_budget(1000000, 20000, "qwen3-max", TokenCounter()) == 980000


def test_retry_guard_forces_payload_under_target():
    """The no-shrink guard: whatever compact() leaves behind, the retry payload
    must end up under target -- never resent byte-identical."""
    from berserker.agent.executor import AgentExecutor, ensure_retry_fits

    msgs = _history()
    counter = TokenCounter()
    target = 3000
    assert counter.count_messages(msgs, "gpt-4o") > target

    executor = AgentExecutor.__new__(AgentExecutor)
    result = ensure_retry_fits(
        msgs, msgs[0], target, counter, "gpt-4o", executor._fallback_truncate_messages
    )

    assert counter.count_messages(result, "gpt-4o") <= target
    assert result[0].role == "system"


def test_retry_guard_noop_when_already_under():
    from berserker.agent.executor import ensure_retry_fits

    small = [ChatMessage(role="system", content="sys"),
             ChatMessage(role="user", content="hi")]
    counter = TokenCounter()
    result = ensure_retry_fits(small, small[0], 100000, counter, "gpt-4o", None)
    assert result is small


def test_loop_increment_trigger_scales_with_window():
    """Per-iteration increment guard: fires when one checkpoint interval adds
    more than max(prune_protect, 10% of the window)."""
    from berserker.agent.executor import loop_increment_trigger

    cfg = CompactionConfig()  # prune_protect=40000
    assert loop_increment_trigger(cfg, 1000000) == 100000
    assert loop_increment_trigger(cfg, 128000) == 40000
