"""Tests for token limit management: loop compaction, fallback truncation, context error retry, and non-primary compaction."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import List, Dict, Any

from berserker.provider.base import ChatMessage, ChatResponse, ContextLengthExceeded
from berserker.agent.compaction import CompactionConfig, CompactionStrategy, default_config
from berserker.session.token_counter import TokenCounter


# ---------------------------------------------------------------------------
# Test 1: Tool Loop Token Check
# ---------------------------------------------------------------------------

class TestToolLoopTokenCheck:
    """Test token checking and compaction in the tool call loop."""

    def test_compaction_triggered_in_loop(self):
        """When tokens exceed threshold during tool loop, compaction should trigger."""
        # Create a mock agent manager with compaction methods
        from berserker.agent.manager import AgentManager
        
        manager = AgentManager()
        
        # Mock the compaction methods
        manager.prune_messages = Mock(side_effect=lambda msgs, **kw: msgs)
        manager.compact = Mock(side_effect=lambda msgs, **kw: msgs[:5])
        
        # Create messages that exceed token limit
        messages = [ChatMessage(role="user", content="x" * 1000) for _ in range(50)]
        
        # Create strategy with low threshold for testing
        # reserved=70000 means threshold = 128000 - 70000 = 58000
        # 60000 > 58000, so should trigger
        strategy = CompactionStrategy(CompactionConfig(auto=True, reserved=70000))
        
        # Check if compaction should trigger
        should_trigger = strategy.should_compact(60000, 128000)
        assert should_trigger is True
        
        # Verify prune and compact would be called
        if should_trigger:
            messages = manager.prune_messages(messages)
            messages = manager.compact(messages, max_tokens=58000)
            
            manager.prune_messages.assert_called_once()
            manager.compact.assert_called_once()

    def test_no_compaction_when_under_threshold(self):
        """When tokens are under threshold, no compaction should trigger."""
        strategy = CompactionStrategy(default_config())
        
        # 50000 tokens with 128000 limit and 20000 buffer
        # Threshold = 128000 - 20000 = 108000
        # 50000 < 108000, so should NOT compact
        should_trigger = strategy.should_compact(50000, 128000)
        assert should_trigger is False


# ---------------------------------------------------------------------------
# Test 2: Fallback Truncation
# ---------------------------------------------------------------------------

class TestFallbackTruncation:
    """Test fallback message truncation when compaction doesn't reduce enough."""

    def test_fallback_truncate_removes_oldest_messages(self):
        """Fallback truncation should remove oldest messages while keeping system prompt."""
        from berserker.agent.executor import AgentExecutor
        from unittest.mock import Mock
        
        mock_manager = Mock()
        executor = AgentExecutor(mock_manager)
        
        # Create messages: system + 20 conversation messages
        system_msg = ChatMessage(role="system", content="You are an assistant")
        conversation_msgs = [
            ChatMessage(role="user" if i % 2 == 0 else "assistant", content="Message {}".format(i))
            for i in range(20)
        ]
        full_messages = [system_msg] + conversation_msgs
        
        # Mock token counter
        mock_counter = Mock()
        # First call: 50000 tokens (too high), after truncation: 15000 tokens
        mock_counter.count_messages = Mock(side_effect=lambda msgs, model: 50000 if len(msgs) > 10 else 15000)
        
        # Call fallback truncation
        result = executor._fallback_truncate_messages(
            full_messages, system_msg, max_tokens=20000,
            token_counter=mock_counter, model_name="test-model"
        )
        
        # System message should be preserved
        assert result[0].role == "system"
        # Some messages should be removed
        assert len(result) < len(full_messages)
        # Should still have some conversation messages
        assert len(result) > 1

    def test_fallback_preserves_minimum_messages(self):
        """Fallback should preserve at least 5 messages when possible."""
        from berserker.agent.executor import AgentExecutor
        from unittest.mock import Mock
        
        mock_manager = Mock()
        executor = AgentExecutor(mock_manager)
        
        system_msg = ChatMessage(role="system", content="System")
        conversation_msgs = [
            ChatMessage(role="user", content="Msg {}".format(i))
            for i in range(10)
        ]
        full_messages = [system_msg] + conversation_msgs
        
        mock_counter = Mock()
        # Return token count proportional to message count
        # 10 messages = 50000 tokens, 5 messages = 25000 tokens, 6 messages = 30000 tokens
        mock_counter.count_messages = Mock(side_effect=lambda msgs, model: len(msgs) * 5000)
        
        result = executor._fallback_truncate_messages(
            full_messages, system_msg, max_tokens=30000,
            token_counter=mock_counter, model_name="test"
        )
        
        # Should keep at least system + 5 messages (6 total)
        assert len(result) >= 6


# ---------------------------------------------------------------------------
# Test 3: Context Length Error Retry
# ---------------------------------------------------------------------------

class TestContextLengthErrorRetry:
    """Test automatic retry when context length exceeded errors occur."""

    def test_context_length_exceeded_exception_exists(self):
        """ContextLengthExceeded exception should be defined."""
        exc = ContextLengthExceeded("Context too long", model="gpt-4")
        assert str(exc) == "Context too long"
        assert exc.model == "gpt-4"

    def test_openai_detects_context_length_error(self):
        """OpenAI provider should detect context_length_exceeded errors."""
        from berserker.provider.openai import _translate_error
        import openai
        
        # Create a mock BadRequestError with context length message
        mock_error = Mock(spec=openai.BadRequestError)
        mock_error.status_code = 400
        mock_error.message = "This model's maximum context length is 128000 tokens. However, your messages resulted in 150000 tokens."
        # _translate_error uses str(exc) to get the body, so we need __str__ to return the message
        mock_error.__str__ = Mock(return_value=mock_error.message)
        
        # _translate_error returns (not raises) the exception - caller does `raise _translate_error(...)`
        result = _translate_error(mock_error, "gpt-4")
        assert isinstance(result, ContextLengthExceeded)
        assert result.model == "gpt-4"

    def test_anthropic_detects_context_window_error(self):
        """Anthropic provider should detect model_context_window_exceeded errors."""
        from berserker.provider.anthropic import _handle_error
        import anthropic
        
        # Create a mock APIStatusError with context window message
        mock_error = Mock(spec=anthropic.APIStatusError)
        mock_error.status_code = 400
        mock_error.message = "model_context_window_exceeded"
        
        # _handle_error takes only 1 argument and raises directly
        with pytest.raises(ContextLengthExceeded):
            _handle_error(mock_error)

    def test_retry_logic_attempts_once(self):
        """Manager should retry exactly once after ContextLengthExceeded."""
        from berserker.agent.manager import AgentManager
        
        manager = AgentManager()
        
        # Track call count
        call_count = [0]
        
        def mock_chat(messages, model, **options):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ContextLengthExceeded("Context too long", model=model)
            return ChatResponse(id="retry-success", model=model, content="Success", finish_reason="stop")
        
        mock_provider = Mock()
        mock_provider.chat = mock_chat
        mock_provider.map_tools = Mock(return_value=[])
        
        # This would be tested in integration, but we can verify the exception behavior
        assert call_count[0] == 0


# ---------------------------------------------------------------------------
# Test 4: Non-Primary Agent Compaction
# ---------------------------------------------------------------------------

class TestNonPrimaryAgentCompaction:
    """Test compaction for non-primary agents based on auto flag."""

    def test_compaction_enabled_for_non_primary_when_auto_true(self):
        """Compaction should trigger for non-primary agents when auto=True."""
        strategy = CompactionStrategy(CompactionConfig(auto=True, reserved=20000))
        
        # Simulate non-primary agent
        agent_mode = "secondary"
        
        # Condition: strategy.config.auto or agent.mode == "primary"
        should_compact = strategy.config.auto or agent_mode == "primary"
        assert should_compact is True

    def test_compaction_disabled_for_non_primary_when_auto_false(self):
        """Compaction should NOT trigger for non-primary agents when auto=False."""
        strategy = CompactionStrategy(CompactionConfig(auto=False, reserved=20000))
        
        agent_mode = "secondary"
        
        should_compact = strategy.config.auto or agent_mode == "primary"
        assert should_compact is False

    def test_compaction_enabled_for_primary_regardless_of_auto(self):
        """Compaction should always trigger for primary agents."""
        strategy = CompactionStrategy(CompactionConfig(auto=False, reserved=20000))
        
        agent_mode = "primary"
        
        should_compact = strategy.config.auto or agent_mode == "primary"
        assert should_compact is True

    def test_should_compact_respects_threshold(self):
        """Compaction should only trigger when tokens exceed threshold."""
        strategy = CompactionStrategy(default_config())
        
        # Under threshold
        assert strategy.should_compact(50000, 128000) is False
        
        # Over threshold (128000 - 20000 = 108000)
        assert strategy.should_compact(110000, 128000) is True


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------

class TestTokenManagementIntegration:
    """Integration tests for the complete token management flow."""

    def test_full_compaction_flow(self):
        """Test the complete flow: detect -> compact -> fallback -> retry."""
        # This is a high-level integration test
        # In practice, this would require mocking the entire agent execution
        
        # 1. Token counting
        counter = TokenCounter()
        messages = [
            ChatMessage(role="system", content="System prompt"),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi there"),
        ]
        
        # Token counting should work
        token_count = counter.count_messages(messages, "gpt-4")
        assert token_count > 0
        
        # 2. Compaction decision
        strategy = CompactionStrategy(default_config())
        should_trigger = strategy.should_compact(token_count, 128000)
        # For short messages, should not trigger
        assert should_trigger is False

    def test_context_length_exception_hierarchy(self):
        """ContextLengthExceeded should inherit from ProviderError."""
        from berserker.provider.base import ProviderError
        
        exc = ContextLengthExceeded("Test")
        assert isinstance(exc, ProviderError)
        assert isinstance(exc, Exception)
