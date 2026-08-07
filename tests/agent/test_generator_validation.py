"""
Tests for berserker.agent.generator module — validation and error handling.

Covers:
- Invalid JSON response from LLM.
- Missing required fields in generated schema.
- Empty response from LLM.
- Non-JSON response (plain text).
- Markdown code fence handling.
- Invalid field values (bad mode, bad permission, etc.).

Python 3.8.10 compatible: uses type comments, pytest fixtures, mocking.
"""

import json
import pytest

from berserker.agent.generator import AgentGenerator, AgentGenerationError
from berserker.agent.registry import AgentRegistry
from berserker.provider.base import ChatResponse


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_registry():
    """Return an empty AgentRegistry."""
    return AgentRegistry()


@pytest.fixture
def mock_provider():
    """Return a mock provider object."""
    provider = type("MockProvider", (), {})()
    provider.id = "mock"
    provider.name = "Mock Provider"
    provider.models = ["gpt-4o"]
    return provider


@pytest.fixture
def mock_provider_registry(mock_provider):
    """Return a mock provider registry."""
    registry = type("MockProviderRegistry", (), {})()
    registry._providers = {"mock": mock_provider}
    registry.list_providers = lambda: list(registry._providers.keys())
    registry.get = lambda pid: registry._providers[pid]
    return registry


@pytest.fixture
def generator(agent_registry, mock_provider_registry):
    """Return an AgentGenerator instance."""
    return AgentGenerator(agent_registry, mock_provider_registry)


# ---------------------------------------------------------------------------
# Helper: mock provider.chat()
# ---------------------------------------------------------------------------


def _mock_chat(response_content):
    # type: (str) -> callable
    """Create a mock chat function that returns the given content."""
    def chat(messages, model, **options):
        # type: (list, str, **any) -> ChatResponse
        return ChatResponse(
            id="mock-response",
            model=model,
            content=response_content,
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            finish_reason="stop",
        )
    return chat


# ---------------------------------------------------------------------------
# Test: Invalid JSON response
# ---------------------------------------------------------------------------


class TestInvalidJsonResponse:
    """Test handling of invalid JSON responses."""

    def test_invalid_json_raises(self, generator, mock_provider):
        """_validate_response raises AgentGenerationError for invalid JSON."""
        mock_provider.chat = _mock_chat("This is not JSON at all")

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "not valid json" in str(exc_info.value).lower()

    def test_malformed_json_raises(self, generator, mock_provider):
        """_validate_response raises AgentGenerationError for malformed JSON."""
        mock_provider.chat = _mock_chat('{"name": "test", "description":')

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "not valid json" in str(exc_info.value).lower()

    def test_json_array_raises(self, generator, mock_provider):
        """_validate_response raises AgentGenerationError for JSON array."""
        mock_provider.chat = _mock_chat('["item1", "item2"]')

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "not a json object" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test: Missing required fields
# ---------------------------------------------------------------------------


class TestMissingFields:
    """Test handling of missing required fields."""

    def test_missing_name_raises(self, generator, mock_provider):
        """Missing 'name' field raises AgentGenerationError."""
        response = json.dumps({
            "description": "A test agent",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a test agent.",
        })
        mock_provider.chat = _mock_chat(response)

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "validation failed" in str(exc_info.value).lower()
        assert "name" in str(exc_info.value).lower()

    def test_missing_description_raises(self, generator, mock_provider):
        """Missing 'description' field raises AgentGenerationError."""
        response = json.dumps({
            "name": "test-agent",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a test agent.",
        })
        mock_provider.chat = _mock_chat(response)

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "validation failed" in str(exc_info.value).lower()
        assert "description" in str(exc_info.value).lower()

    def test_missing_mode_raises(self, generator, mock_provider):
        """Missing 'mode' field raises AgentGenerationError."""
        response = json.dumps({
            "name": "test-agent",
            "description": "A test agent",
            "model": "gpt-4o",
            "prompt": "You are a test agent.",
        })
        mock_provider.chat = _mock_chat(response)

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "validation failed" in str(exc_info.value).lower()
        assert "mode" in str(exc_info.value).lower()

    def test_missing_model_raises(self, generator, mock_provider):
        """Missing 'model' field raises AgentGenerationError."""
        response = json.dumps({
            "name": "test-agent",
            "description": "A test agent",
            "mode": "subagent",
            "prompt": "You are a test agent.",
        })
        mock_provider.chat = _mock_chat(response)

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "validation failed" in str(exc_info.value).lower()
        assert "model" in str(exc_info.value).lower()

    def test_missing_prompt_raises(self, generator, mock_provider):
        """Missing 'prompt' field raises AgentGenerationError."""
        response = json.dumps({
            "name": "test-agent",
            "description": "A test agent",
            "mode": "subagent",
            "model": "gpt-4o",
        })
        mock_provider.chat = _mock_chat(response)

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "validation failed" in str(exc_info.value).lower()
        assert "prompt" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test: Empty response
# ---------------------------------------------------------------------------


class TestEmptyResponse:
    """Test handling of empty responses."""

    def test_empty_string_raises(self, generator, mock_provider):
        """Empty response raises AgentGenerationError."""
        mock_provider.chat = _mock_chat("")

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "empty" in str(exc_info.value).lower()

    def test_whitespace_only_raises(self, generator, mock_provider):
        """Whitespace-only response raises AgentGenerationError."""
        mock_provider.chat = _mock_chat("   \n\n   ")

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "empty" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test: Non-JSON response (plain text)
# ---------------------------------------------------------------------------


class TestNonJsonResponse:
    """Test handling of non-JSON plain text responses."""

    def test_plain_text_raises(self, generator, mock_provider):
        """Plain text response raises AgentGenerationError."""
        mock_provider.chat = _mock_chat(
            "Here is your agent configuration:\n"
            "Name: test-agent\n"
            "Mode: subagent\n"
            "Model: gpt-4o"
        )

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "not valid json" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test: Markdown code fence handling
# ---------------------------------------------------------------------------


class TestMarkdownCodeFence:
    """Test handling of markdown code fences in LLM responses."""

    def test_code_fence_with_json(self, generator, mock_provider):
        """Response wrapped in ```json ... ``` is parsed correctly."""
        valid_json = json.dumps({
            "name": "fenced-agent",
            "description": "An agent with fenced response",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a fenced agent.",
            "permission": "restricted",
            "tools": ["read"],
        })
        fenced_response = "```json\n{}\n```".format(valid_json)
        mock_provider.chat = _mock_chat(fenced_response)

        schema = generator.generate("Create an agent")

        assert schema.name == "fenced-agent"

    def test_code_fence_without_language(self, generator, mock_provider):
        """Response wrapped in ``` ... ``` (no language) is parsed correctly."""
        valid_json = json.dumps({
            "name": "plain-fenced-agent",
            "description": "An agent with plain fenced response",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a plain fenced agent.",
            "permission": "restricted",
            "tools": ["read"],
        })
        fenced_response = "```\n{}\n```".format(valid_json)
        mock_provider.chat = _mock_chat(fenced_response)

        schema = generator.generate("Create an agent")

        assert schema.name == "plain-fenced-agent"


# ---------------------------------------------------------------------------
# Test: Invalid field values
# ---------------------------------------------------------------------------


class TestInvalidFieldValues:
    """Test handling of invalid field values."""

    def test_invalid_mode_raises(self, generator, mock_provider):
        """Invalid mode value raises AgentGenerationError."""
        response = json.dumps({
            "name": "test-agent",
            "description": "A test agent",
            "mode": "invalid-mode",
            "model": "gpt-4o",
            "prompt": "You are a test agent.",
        })
        mock_provider.chat = _mock_chat(response)

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "validation failed" in str(exc_info.value).lower()

    def test_invalid_permission_raises(self, generator, mock_provider):
        """Invalid permission value raises AgentGenerationError."""
        response = json.dumps({
            "name": "test-agent",
            "description": "A test agent",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a test agent.",
            "permission": "admin",
        })
        mock_provider.chat = _mock_chat(response)

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "validation failed" in str(exc_info.value).lower()

    def test_invalid_name_format_raises(self, generator, mock_provider):
        """Invalid name format raises AgentGenerationError."""
        response = json.dumps({
            "name": "Invalid_Name",
            "description": "A test agent",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a test agent.",
        })
        mock_provider.chat = _mock_chat(response)

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "validation failed" in str(exc_info.value).lower()

    def test_temperature_out_of_range_raises(self, generator, mock_provider):
        """Temperature > 2.0 raises AgentGenerationError."""
        response = json.dumps({
            "name": "test-agent",
            "description": "A test agent",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are a test agent.",
            "temperature": 5.0,
        })
        mock_provider.chat = _mock_chat(response)

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")

        assert "validation failed" in str(exc_info.value).lower()
