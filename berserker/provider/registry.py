"""
Provider Registry for berserker.

Manages provider instances with dynamic loading from configuration.
Supports all major LLM providers and OpenAI-compatible endpoints.

Python 3.8.10 compatible: uses type comments, Optional, no match/case.
"""

import importlib
from typing import Any, Dict, List, Optional, Tuple

from berserker.provider.base import ModelNotFoundError, Provider, ProviderError

# ---------------------------------------------------------------------------
# Provider Type Mapping
# ---------------------------------------------------------------------------

# Map provider type strings to (module_path, class_name) for dynamic import.
# Native providers have their own classes; OpenAI-compatible providers all
# use OpenAICompatibleProvider with different base URLs.
_PROVIDER_TYPE_MAP = {
    "openai": ("berserker.provider.openai", "OpenAIProvider"),
    "anthropic": ("berserker.provider.anthropic", "AnthropicProvider"),
    "google": ("berserker.provider.google", "GoogleProvider"),
    "azure": ("berserker.provider.azure", "AzureOpenAIProvider"),
    "mistral": ("berserker.provider.mistral", "MistralProvider"),
    "amazon_bedrock": ("berserker.provider.amazon_bedrock", "AmazonBedrockProvider"),
}  # type: Dict[str, Tuple[str, str]]

# OpenAI-compatible provider types with their default base URLs.
_OPENAI_COMPATIBLE_PROVIDERS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "deepseek": "https://api.deepseek.com",
    "cerebras": "https://api.cerebras.ai/v1",
    "together": "https://api.together.xyz/v1",
    "perplexity": "https://api.perplexity.ai",
    "xai": "https://api.x.ai/v1",
    "venice": "https://api.venice.ai/api/v1",
    "gitlab": "https://gitlab.com/api/v4/ai",
    "bailian": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "bailian_coding": "https://coding.dashscope.aliyuncs.com/v1",
}  # type: Dict[str, str]

# Default models for native provider types when not specified in config.
_DEFAULT_MODELS = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo", "o1", "o3-mini"],
    "anthropic": [
        "claude-sonnet-4-20250514",
        "claude-3.5-sonnet-20241022",
        "claude-3.5-haiku-20241022",
        "claude-3-opus-20240229",
    ],
    "google": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
    "azure": ["gpt-4o", "gpt-4-turbo", "gpt-35-turbo"],
    "mistral": ["mistral-large-latest", "mistral-small-latest", "open-mistral-nemo"],
    "amazon_bedrock": [
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "anthropic.claude-3-sonnet-20240229-v1:0",
        "meta.llama3-70b-instruct-v1:0",
    ],
}  # type: Dict[str, List[str]]


def _get_provider_class(provider_type):
    # type: (str) -> type
    """Dynamically import and return a provider class by type string.

    Args:
        provider_type: Provider type identifier (e.g. "openai", "anthropic").

    Returns:
        The provider class (not an instance).

    Raises:
        ProviderError: If the provider type is unknown.
    """
    # Check native providers first
    if provider_type in _PROVIDER_TYPE_MAP:
        module_path, class_name = _PROVIDER_TYPE_MAP[provider_type]
        module = importlib.import_module(module_path)
        return getattr(module, class_name)

    # Check OpenAI-compatible providers
    if provider_type in _OPENAI_COMPATIBLE_PROVIDERS:
        module = importlib.import_module("berserker.provider.openai_compatible")
        return getattr(module, "OpenAICompatibleProvider")

    raise ProviderError("Unknown provider type: '{}'".format(provider_type))


def _instantiate_provider(provider_type, provider_id, config):
    # type: (str, str, Dict[str, Any]) -> Provider
    """Instantiate a provider from its type and configuration.

    Args:
        provider_type: Provider type string (e.g. "openai", "groq").
        provider_id: Unique identifier for this provider instance.
        config: Configuration dict with keys like api_key, base_url, etc.

    Returns:
        An instantiated Provider subclass.

    Raises:
        ProviderError: If instantiation fails.
    """
    provider_class = _get_provider_class(provider_type)

    # Build kwargs from config, filtering out 'type' which is not a constructor arg
    kwargs = {}  # type: Dict[str, Any]
    for key, value in config.items():
        if key != "type":
            kwargs[key] = value

    # Handle OpenAI-compatible providers specially
    if provider_type in _OPENAI_COMPATIBLE_PROVIDERS:
        # OpenAICompatibleProvider requires: id, name, base_url
        base_url = kwargs.pop("base_url", _OPENAI_COMPATIBLE_PROVIDERS[provider_type])
        name = kwargs.pop("name", provider_type.capitalize())
        models = kwargs.pop("models", None)

        return provider_class(id=provider_id, name=name, base_url=base_url, models=models, **kwargs)

    # For native providers, pass id and remaining kwargs
    # Native providers don't accept 'name' or 'models' in __init__ —
    # filter them out, then set models on the instance after creation.
    models = kwargs.pop("models", None)
    kwargs.pop("name", None)
    kwargs["id"] = provider_id
    provider = provider_class(**kwargs)
    # Apply default models if not specified in config
    if models is None:
        models = _DEFAULT_MODELS.get(provider_type, [])
    provider.models = models
    return provider


# ---------------------------------------------------------------------------
# ProviderRegistry
# ---------------------------------------------------------------------------


class ProviderRegistry(object):
    """Registry managing provider instances.

    Providers are stored in a dict keyed by provider_id.
    Supports dynamic loading from configuration dictionaries.

    Usage:
        registry = ProviderRegistry()
        registry.register("openai", OpenAIProvider())
        provider = registry.get("openai")
    """

    def __init__(self):
        # type: () -> None
        """Initialize an empty provider registry."""
        self._providers = {}  # type: Dict[str, Provider]
        self._model_metadata = {}  # type: Dict[str, Dict[str, Any]]  # model_id -> metadata

    def register(self, provider_id, provider):
        # type: (str, Provider) -> None
        """Register a provider instance.

        Args:
            provider_id: Unique identifier for the provider.
            provider: A Provider instance to register.

        Raises:
            ProviderError: If provider is not a Provider instance.
        """
        if not isinstance(provider, Provider):
            raise ProviderError(
                "Provider '{}' must be an instance of Provider, got {}".format(
                    provider_id, type(provider).__name__
                )
            )
        # Close the previous instance on re-register so its HTTP client
        # connections are released instead of leaked.
        old = self._providers.get(provider_id)
        if old is not None and old is not provider and hasattr(old, "close"):
            old.close()
        self._providers[provider_id] = provider

    def get(self, provider_id):
        # type: (str) -> Provider
        """Get a provider by ID.

        Args:
            provider_id: The provider identifier.

        Returns:
            The registered Provider instance.

        Raises:
            ProviderError: If the provider is not found.
        """
        if provider_id not in self._providers:
            raise ProviderError("Provider '{}' not found".format(provider_id))
        return self._providers[provider_id]

    def list_providers(self):
        # type: () -> List[str]
        """List all registered provider IDs.

        Returns:
            List of provider ID strings.
        """
        return list(self._providers.keys())

    def list_all_models(self):
        # type: () -> Dict[str, List[str]]
        """List all models from all registered providers.

        Returns:
            Dict mapping provider_id to list of model names.
        """
        result = {}  # type: Dict[str, List[str]]
        for provider_id, provider in self._providers.items():
            result[provider_id] = provider.list_models()
        return result

    def get_provider_for_model(self, model_id):
        # type: (str) -> Tuple[Provider, str]
        """Find which provider supports a given model.

        Searches all registered providers' model lists for the given model_id.

        Args:
            model_id: The model identifier to search for.

        Returns:
            Tuple of (provider, model_name) where model_name is the matched
            model string from the provider's list.

        Raises:
            ModelNotFoundError: If no provider supports the given model.
        """
        for provider_id, provider in self._providers.items():
            models = provider.list_models()
            for model in models:
                if model == model_id:
                    return (provider, model)
        raise ModelNotFoundError("Model '{}' not found in any registered provider".format(model_id))

    def get_model_metadata(self, model_id):
        # type: (str) -> Dict[str, Any]
        """Get metadata for a model (context_window, max_output_tokens, etc.).

        Args:
            model_id: The model identifier.

        Returns:
            Dict with model metadata. Keys include:
                - context_window: Maximum input tokens the model accepts
                - max_output_tokens: Maximum output tokens the model generates
            Returns defaults if model not found in config.
        """
        # Check if we have explicit metadata for this model
        if model_id in self._model_metadata:
            return self._model_metadata[model_id]

        # Try partial match (some models have version suffixes)
        for known_model, metadata in self._model_metadata.items():
            if model_id.startswith(known_model) or known_model.startswith(model_id):
                return metadata

        # Return defaults for unknown models
        return {
            "context_window": 128000,
            "max_output_tokens": 4096,
            "compaction_buffer": 20000,
        }

    def register_model_metadata(self, model_id, metadata):
        # type: (str, Dict[str, Any]) -> None
        """Register metadata for a model.

        Args:
            model_id: The model identifier.
            metadata: Dict with model metadata (context_window, max_output_tokens, etc.).
        """
        self._model_metadata[model_id] = metadata

    def load_from_config(self, config):
        # type: (Dict[str, Any]) -> None
        """Instantiate and register providers from a configuration dict.

        Expected config format:
            {
                "providers": {
                    "openai": {
                        "type": "openai",
                        "api_key": "sk-..."
                    },
                    "my-groq": {
                        "type": "groq",
                        "api_key": "gsk-...",
                        "models": ["llama-3.1-70b"]
                    }
                }
            }

        Each provider entry must have a "type" field. The key in the
        providers dict becomes the provider_id.

        Supported types:
            - Native: "openai", "anthropic", "google", "azure", "mistral", "amazon_bedrock"
            - OpenAI-compatible: "groq", "openrouter", "deepinfra", "cerebras",
              "together", "perplexity", "xai", "venice", "gitlab"

        Args:
            config: Configuration dictionary with a "providers" key.

        Raises:
            ProviderError: If config is invalid or a provider fails to load.
        """
        providers_config = config.get("providers", {})
        if not providers_config:
            return

        for provider_id, provider_config in providers_config.items():
            provider_type = provider_config.get("type")
            if provider_type is None:
                raise ProviderError(
                    "Provider '{}' missing required 'type' field".format(provider_id)
                )

            provider = _instantiate_provider(provider_type, provider_id, provider_config)
            self.register(provider_id, provider)

            # Register model metadata if models are specified with metadata
            models_config = provider_config.get("models", [])
            if models_config:
                for model_entry in models_config:
                    if isinstance(model_entry, dict):
                        # Model with metadata: {"name": "gpt-4o", "context_window": 128000, ...}
                        model_name = model_entry.get("name")
                        if model_name:
                            metadata = {
                                "context_window": max(
                                    0, int(model_entry.get("context_window", 128000))
                                ),
                                "max_output_tokens": max(
                                    0, int(model_entry.get("max_output_tokens", 4096))
                                ),
                                "compaction_buffer": max(
                                    0, int(model_entry.get("compaction_buffer", 20000))
                                ),
                            }
                            self.register_model_metadata(model_name, metadata)
                    elif isinstance(model_entry, str):
                        # Simple model name without metadata
                        self.register_model_metadata(
                            model_entry,
                            {
                                "context_window": 128000,
                                "max_output_tokens": 4096,
                                "compaction_buffer": 20000,
                            },
                        )


# ---------------------------------------------------------------------------
# Singleton Instance
# ---------------------------------------------------------------------------

registry = ProviderRegistry()
