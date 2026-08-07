"""
Tests for berserker.agent.generator module — successful generation paths.

Covers:
- AgentGenerator.generate() with mocked LLM responses.
- AgentGenerator.generate_and_register() with mocked LLM responses.
- Generation with different providers and models.
- Empty description handling.

Python 3.8.10 compatible: uses type comments, pytest fixtures, mocking.
"""

import json
import pytest

from berserker.agent.generator import AgentGenerator, AgentGenerationError
from berserker.agent.registry import AgentRegistry
from berserker.agent.schema import AgentSchema
from berserker.provider.base import ChatResponse, ProviderError


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
    provider.models = ["gpt-4o", "gpt-4o-mini"]
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
def valid_agent_json():
    """Return a valid agent configuration as JSON string."""
    return json.dumps({
        "name": "test-reviewer",
        "description": "A code review agent",
        "mode": "subagent",
        "model": "gpt-4o",
        "prompt": "You are a code review specialist. Review code for quality, security, and best practices.",
        "permission": "restricted",
        "tools": ["read", "grep", "glob"],
        "temperature": 0.5,
    })


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
# Test: generate() — successful generation
# ---------------------------------------------------------------------------


class TestGenerateSuccess:
    """Test successful agent generation."""

    def test_generate_returns_schema(self, generator, mock_provider, valid_agent_json):
        """generate() returns a valid AgentSchema."""
        mock_provider.chat = _mock_chat(valid_agent_json)
        schema = generator.generate("Create a code review agent")

        assert isinstance(schema, AgentSchema)
        assert schema.name == "test-reviewer"
        assert schema.mode == "subagent"
        assert schema.model == "gpt-4o"
        assert "code review" in schema.description.lower()

    def test_generate_with_specific_provider(self, generator, mock_provider, valid_agent_json):
        """generate() uses the specified provider."""
        mock_provider.chat = _mock_chat(valid_agent_json)
        schema = generator.generate(
            "Create a code review agent",
            provider="mock",
        )

        assert isinstance(schema, AgentSchema)
        assert schema.name == "test-reviewer"

    def test_generate_with_specific_model(self, generator, mock_provider, valid_agent_json):
        """generate() uses the specified model."""
        mock_provider.chat = _mock_chat(valid_agent_json)
        schema = generator.generate(
            "Create a code review agent",
            model="gpt-4o-mini",
        )

        assert isinstance(schema, AgentSchema)

    def test_generate_with_provider_and_model(self, generator, mock_provider, valid_agent_json):
        """generate() uses both specified provider and model."""
        mock_provider.chat = _mock_chat(valid_agent_json)
        schema = generator.generate(
            "Create a code review agent",
            provider="mock",
            model="gpt-4o-mini",
        )

        assert isinstance(schema, AgentSchema)


# ---------------------------------------------------------------------------
# Test: generate_and_register() — successful generation and registration
# ---------------------------------------------------------------------------


class TestGenerateAndRegister:
    """Test generate_and_register() end-to-end."""

    def test_generate_and_register_returns_name(self, generator, mock_provider, valid_agent_json):
        """generate_and_register() returns the agent name."""
        mock_provider.chat = _mock_chat(valid_agent_json)
        name = generator.generate_and_register("Create a code review agent")

        assert name == "test-reviewer"

    def test_generate_and_register_registers_agent(self, generator, mock_provider, valid_agent_json, agent_registry):
        """generate_and_register() actually registers the agent."""
        mock_provider.chat = _mock_chat(valid_agent_json)
        generator.generate_and_register("Create a code review agent")

        assert agent_registry.has("test-reviewer")
        assert agent_registry.count() == 1

    def test_generate_and_register_creates_custom_agent(self, generator, mock_provider, valid_agent_json, agent_registry):
        """generate_and_register() creates a CustomAgent (native=False)."""
        mock_provider.chat = _mock_chat(valid_agent_json)
        generator.generate_and_register("Create a code review agent")

        agent = agent_registry.get("test-reviewer")
        from berserker.agent.base import CustomAgent
        assert isinstance(agent, CustomAgent)


# ---------------------------------------------------------------------------
# Test: empty description handling
# ---------------------------------------------------------------------------


class TestEmptyDescription:
    """Test handling of empty descriptions."""

    def test_generate_empty_string_raises(self, generator):
        """generate() raises AgentGenerationError for empty string."""
        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("")
        assert "empty" in str(exc_info.value).lower()

    def test_generate_whitespace_raises(self, generator):
        """generate() raises AgentGenerationError for whitespace-only."""
        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("   ")
        assert "empty" in str(exc_info.value).lower()

    def test_generate_none_raises(self, generator):
        """generate() raises AgentGenerationError for None."""
        with pytest.raises(AgentGenerationError):
            generator.generate(None)


# ---------------------------------------------------------------------------
# Test: no providers available
# ---------------------------------------------------------------------------


class TestNoProviders:
    """Test handling when no providers are available."""

    def test_generate_no_providers_raises(self, agent_registry):
        """generate() raises AgentGenerationError when no providers exist."""
        empty_registry = type("EmptyProviderRegistry", (), {})()
        empty_registry.list_providers = lambda: []

        generator = AgentGenerator(agent_registry, empty_registry)

        with pytest.raises(AgentGenerationError) as exc_info:
            generator.generate("Create an agent")
        assert "no llm providers" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test: invalid provider ID
# ---------------------------------------------------------------------------


class TestInvalidProvider:
    """Test handling of invalid provider IDs."""

    def test_generate_invalid_provider_raises(self, generator, mock_provider_registry):
        """generate() raises AgentGenerationError for unknown provider."""
        def raise_provider_error(pid):
            raise ProviderError("Provider '{}' not found".format(pid))
        mock_provider_registry.get = raise_provider_error

        with pytest.raises(AgentGenerationError):
            generator.generate("Create an agent", provider="unknown")
