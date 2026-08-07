"""Comprehensive unit tests for ProviderRegistry class."""

from __future__ import annotations

import pytest
from typing import Dict, Any, List

from berserker.provider.registry import ProviderRegistry
from berserker.provider.base import Provider, ProviderError, ModelNotFoundError

from tests.helpers.mock_provider import MockProvider


# ---------------------------------------------------------------------------
# Registration Tests
# ---------------------------------------------------------------------------


class TestRegisterProvider:
    """Test ProviderRegistry.register() method."""

    def test_register_provider(self):
        """Normal registration, verify get() returns same instance."""
        registry = ProviderRegistry()
        provider = MockProvider(id="test", name="Test")
        registry.register("test", provider)
        assert registry.get("test") is provider

    def test_register_non_provider(self):
        """Register a non-Provider object, expect ProviderError."""
        registry = ProviderRegistry()
        with pytest.raises(ProviderError) as exc_info:
            registry.register("bad", "not a provider")
        assert "must be an instance of Provider" in str(exc_info.value)

    def test_register_non_provider_dict(self):
        """Register a dict instead of Provider, expect ProviderError."""
        registry = ProviderRegistry()
        with pytest.raises(ProviderError):
            registry.register("bad", {"key": "value"})

    def test_register_duplicate_overwrites(self):
        """Register same ID twice, verify second overwrites first."""
        registry = ProviderRegistry()
        provider1 = MockProvider(id="dup", name="First")
        provider2 = MockProvider(id="dup", name="Second")
        registry.register("dup", provider1)
        registry.register("dup", provider2)
        assert registry.get("dup") is provider2

    def test_register_multiple_providers(self):
        """Register 2+ providers, verify list_providers() returns all."""
        registry = ProviderRegistry()
        p1 = MockProvider(id="p1", name="Provider1")
        p2 = MockProvider(id="p2", name="Provider2")
        p3 = MockProvider(id="p3", name="Provider3")
        registry.register("p1", p1)
        registry.register("p2", p2)
        registry.register("p3", p3)
        providers = registry.list_providers()
        assert len(providers) == 3
        assert "p1" in providers
        assert "p2" in providers
        assert "p3" in providers


# ---------------------------------------------------------------------------
# Lookup Tests
# ---------------------------------------------------------------------------


class TestGetProvider:
    """Test ProviderRegistry.get() method."""

    def test_get_existing_provider(self, mock_provider):
        """Get a registered provider."""
        registry = ProviderRegistry()
        registry.register("mock", mock_provider)
        result = registry.get("mock")
        assert result is mock_provider

    def test_get_nonexistent_provider(self):
        """Get unregistered ID, expect ProviderError."""
        registry = ProviderRegistry()
        with pytest.raises(ProviderError) as exc_info:
            registry.get("nonexistent")
        assert "not found" in str(exc_info.value)

    def test_list_providers_empty(self):
        """List providers on empty registry."""
        registry = ProviderRegistry()
        assert registry.list_providers() == []

    def test_list_providers_multiple(self):
        """List providers with multiple registered."""
        registry = ProviderRegistry()
        registry.register("a", MockProvider(id="a"))
        registry.register("b", MockProvider(id="b"))
        result = registry.list_providers()
        assert set(result) == {"a", "b"}


# ---------------------------------------------------------------------------
# Model Tests
# ---------------------------------------------------------------------------


class TestListAllModels:
    """Test ProviderRegistry.list_all_models() method."""

    def test_list_all_models_empty(self):
        """Empty registry returns empty dict."""
        registry = ProviderRegistry()
        assert registry.list_all_models() == {}

    def test_list_all_models_single_provider(self):
        """Single provider with models returns correct mapping."""
        registry = ProviderRegistry()
        provider = MockProvider(id="p1", models=["model-a", "model-b"])
        registry.register("p1", provider)
        result = registry.list_all_models()
        assert result == {"p1": ["model-a", "model-b"]}

    def test_list_all_models_multiple_providers(self):
        """Multiple providers with multiple models."""
        registry = ProviderRegistry()
        p1 = MockProvider(id="p1", models=["gpt-4o", "gpt-4o-mini"])
        p2 = MockProvider(id="p2", models=["claude-sonnet-4", "claude-3.5-sonnet"])
        registry.register("p1", p1)
        registry.register("p2", p2)
        result = registry.list_all_models()
        assert result == {
            "p1": ["gpt-4o", "gpt-4o-mini"],
            "p2": ["claude-sonnet-4", "claude-3.5-sonnet"],
        }


class TestGetProviderForModel:
    """Test ProviderRegistry.get_provider_for_model() method."""

    def test_get_provider_for_model_exact(self):
        """Exact model match returns correct provider."""
        registry = ProviderRegistry()
        p1 = MockProvider(id="p1", models=["gpt-4o", "gpt-4o-mini"])
        p2 = MockProvider(id="p2", models=["claude-sonnet-4"])
        registry.register("p1", p1)
        registry.register("p2", p2)
        provider, model_name = registry.get_provider_for_model("gpt-4o")
        assert provider is p1
        assert model_name == "gpt-4o"

    def test_get_provider_for_model_second_provider(self):
        """Model found in second registered provider."""
        registry = ProviderRegistry()
        p1 = MockProvider(id="p1", models=["gpt-4o"])
        p2 = MockProvider(id="p2", models=["claude-sonnet-4"])
        registry.register("p1", p1)
        registry.register("p2", p2)
        provider, model_name = registry.get_provider_for_model("claude-sonnet-4")
        assert provider is p2
        assert model_name == "claude-sonnet-4"

    def test_get_provider_for_model_not_found(self):
        """Unknown model raises ModelNotFoundError."""
        registry = ProviderRegistry()
        p1 = MockProvider(id="p1", models=["gpt-4o"])
        registry.register("p1", p1)
        with pytest.raises(ModelNotFoundError) as exc_info:
            registry.get_provider_for_model("unknown-model")
        assert "not found" in str(exc_info.value)

    def test_get_provider_for_model_empty_registry(self):
        """Empty registry raises ModelNotFoundError."""
        registry = ProviderRegistry()
        with pytest.raises(ModelNotFoundError):
            registry.get_provider_for_model("any-model")


# ---------------------------------------------------------------------------
# Metadata Tests
# ---------------------------------------------------------------------------


class TestModelMetadata:
    """Test ProviderRegistry metadata methods."""

    def test_get_model_metadata_exact(self):
        """Exact model match returns registered metadata."""
        registry = ProviderRegistry()
        metadata = {
            "context_window": 200000,
            "max_output_tokens": 8192,
            "compaction_buffer": 30000,
        }
        registry.register_model_metadata("gpt-4o", metadata)
        result = registry.get_model_metadata("gpt-4o")
        assert result == metadata

    def test_get_model_metadata_partial_match_prefix(self):
        """Partial match (model_id starts with known_model) returns closest metadata."""
        registry = ProviderRegistry()
        metadata = {"context_window": 128000, "max_output_tokens": 4096, "compaction_buffer": 20000}
        registry.register_model_metadata("gpt-4o", metadata)
        # "gpt-4o-2024-05-13" starts with "gpt-4o"
        result = registry.get_model_metadata("gpt-4o-2024-05-13")
        assert result == metadata

    def test_get_model_metadata_partial_match_suffix(self):
        """Partial match (known_model starts with model_id) returns closest metadata."""
        registry = ProviderRegistry()
        metadata = {"context_window": 128000, "max_output_tokens": 4096, "compaction_buffer": 20000}
        registry.register_model_metadata("gpt-4o-2024-05-13", metadata)
        # "gpt-4o" is a prefix of "gpt-4o-2024-05-13"
        result = registry.get_model_metadata("gpt-4o")
        assert result == metadata

    def test_get_model_metadata_unknown_returns_defaults(self):
        """Completely unknown model returns defaults."""
        registry = ProviderRegistry()
        result = registry.get_model_metadata("totally-unknown-model")
        assert result == {
            "context_window": 128000,
            "max_output_tokens": 4096,
            "compaction_buffer": 20000,
        }

    def test_register_model_metadata(self):
        """Register and retrieve custom metadata."""
        registry = ProviderRegistry()
        custom_metadata = {
            "context_window": 256000,
            "max_output_tokens": 16384,
            "compaction_buffer": 40000,
        }
        registry.register_model_metadata("custom-model", custom_metadata)
        result = registry.get_model_metadata("custom-model")
        assert result["context_window"] == 256000
        assert result["max_output_tokens"] == 16384
        assert result["compaction_buffer"] == 40000

    def test_register_model_metadata_overwrites(self):
        """Registering metadata for same model overwrites previous."""
        registry = ProviderRegistry()
        registry.register_model_metadata("model", {"context_window": 1000})
        registry.register_model_metadata("model", {"context_window": 2000})
        result = registry.get_model_metadata("model")
        assert result["context_window"] == 2000


# ---------------------------------------------------------------------------
# Config Loading Tests
# ---------------------------------------------------------------------------


class TestLoadFromConfig:
    """Test ProviderRegistry.load_from_config() method."""

    def test_load_from_config_missing_type(self):
        """Config without 'type' field raises ProviderError."""
        registry = ProviderRegistry()
        config = {
            "providers": {
                "my-provider": {
                    "api_key": "sk-test",
                    # missing "type"
                }
            }
        }
        with pytest.raises(ProviderError) as exc_info:
            registry.load_from_config(config)
        assert "missing required 'type' field" in str(exc_info.value)

    def test_load_from_config_unknown_type(self):
        """Unknown provider type raises ProviderError."""
        registry = ProviderRegistry()
        config = {
            "providers": {
                "unknown-provider": {
                    "type": "nonexistent_type",
                    "api_key": "sk-test",
                }
            }
        }
        with pytest.raises(ProviderError) as exc_info:
            registry.load_from_config(config)
        assert "Unknown provider type" in str(exc_info.value)

    def test_load_from_config_empty_providers(self):
        """Config with empty providers dict does nothing."""
        registry = ProviderRegistry()
        config = {"providers": {}}
        registry.load_from_config(config)
        assert registry.list_providers() == []

    def test_load_from_config_no_providers_key(self):
        """Config without 'providers' key does nothing."""
        registry = ProviderRegistry()
        config = {"other_key": "value"}
        registry.load_from_config(config)
        assert registry.list_providers() == []

    def test_load_from_config_native_provider(self):
        """Load a native provider (e.g., openai) from config."""
        registry = ProviderRegistry()
        config = {
            "providers": {
                "openai": {
                    "type": "openai",
                    "api_key": "sk-test-key",
                }
            }
        }
        registry.load_from_config(config)
        assert "openai" in registry.list_providers()
        provider = registry.get("openai")
        assert isinstance(provider, Provider)
        assert provider.id == "openai"

    def test_load_from_config_openai_compatible(self):
        """Load an OpenAI-compatible provider (e.g., groq) from config."""
        registry = ProviderRegistry()
        config = {
            "providers": {
                "my-groq": {
                    "type": "groq",
                    "api_key": "gsk-test-key",
                }
            }
        }
        registry.load_from_config(config)
        assert "my-groq" in registry.list_providers()
        provider = registry.get("my-groq")
        assert isinstance(provider, Provider)
        assert provider.id == "my-groq"

    def test_load_from_config_with_custom_models(self):
        """Load provider with custom model list from config."""
        registry = ProviderRegistry()
        config = {
            "providers": {
                "my-groq": {
                    "type": "groq",
                    "api_key": "gsk-test",
                    "models": ["llama-3.1-70b", "llama-3.1-8b"],
                }
            }
        }
        registry.load_from_config(config)
        models = registry.list_all_models()
        assert "my-groq" in models
        assert "llama-3.1-70b" in models["my-groq"]
        assert "llama-3.1-8b" in models["my-groq"]

    def test_load_from_config_with_model_metadata(self):
        """Load provider with model metadata and verify metadata is registered."""
        registry = ProviderRegistry()
        config = {
            "providers": {
                "openai": {
                    "type": "openai",
                    "api_key": "sk-test",
                    "models": [
                        {
                            "name": "gpt-4o",
                            "context_window": 200000,
                            "max_output_tokens": 8192,
                            "compaction_buffer": 30000,
                        }
                    ],
                }
            }
        }
        registry.load_from_config(config)
        metadata = registry.get_model_metadata("gpt-4o")
        assert metadata["context_window"] == 200000
        assert metadata["max_output_tokens"] == 8192
        assert metadata["compaction_buffer"] == 30000

    def test_load_from_config_multiple_providers(self):
        """Load multiple providers from a single config."""
        registry = ProviderRegistry()
        config = {
            "providers": {
                "openai": {
                    "type": "openai",
                    "api_key": "sk-openai-key",
                },
                "groq": {
                    "type": "groq",
                    "api_key": "gsk-groq-key",
                },
            }
        }
        registry.load_from_config(config)
        providers = registry.list_providers()
        assert len(providers) == 2
        assert "openai" in providers
        assert "groq" in providers

    def test_load_from_config_custom_base_url(self):
        """Load OpenAI-compatible provider with custom base_url."""
        registry = ProviderRegistry()
        config = {
            "providers": {
                "custom-ollama": {
                    "type": "groq",  # uses OpenAICompatibleProvider
                    "api_key": "sk-test",
                    "base_url": "http://localhost:11434/v1",
                }
            }
        }
        registry.load_from_config(config)
        provider = registry.get("custom-ollama")
        assert provider.id == "custom-ollama"


# ---------------------------------------------------------------------------
# Edge Cases and Integration Tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case and integration tests for ProviderRegistry."""

    def test_register_and_get_same_instance(self):
        """Verify that get() returns the exact same object instance."""
        registry = ProviderRegistry()
        provider = MockProvider(id="test", models=["m1", "m2"])
        registry.register("test", provider)
        retrieved = registry.get("test")
        assert retrieved is provider
        assert retrieved.models is provider.models

    def test_get_provider_for_model_returns_tuple(self):
        """Verify return type is (Provider, str) tuple."""
        registry = ProviderRegistry()
        p = MockProvider(id="p", models=["model-x"])
        registry.register("p", p)
        result = registry.get_provider_for_model("model-x")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result[0] is p
        assert result[1] == "model-x"

    def test_list_all_models_returns_copy(self):
        """Verify list_all_models returns a new dict each call."""
        registry = ProviderRegistry()
        p = MockProvider(id="p", models=["m1"])
        registry.register("p", p)
        result1 = registry.list_all_models()
        result2 = registry.list_all_models()
        assert result1 is not result2

    def test_list_providers_returns_copy(self):
        """Verify list_providers returns a new list each call."""
        registry = ProviderRegistry()
        registry.register("p", MockProvider(id="p"))
        result1 = registry.list_providers()
        result2 = registry.list_providers()
        assert result1 is not result2

    def test_metadata_defaults_are_independent(self):
        """Default metadata dict should be a new dict each call."""
        registry = ProviderRegistry()
        m1 = registry.get_model_metadata("unknown-1")
        m2 = registry.get_model_metadata("unknown-2")
        assert m1 is not m2
        m1["context_window"] = 999
        assert registry.get_model_metadata("unknown-3")["context_window"] == 128000
