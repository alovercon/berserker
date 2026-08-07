"""
CLI commands for listing models and providers in berserker.
"""

import logging
import os
from typing import List, Tuple, Dict, Any

from berserker.provider.registry import registry

logger = logging.getLogger(__name__)


def format_table(headers, rows):
    # type: (List[str], List[List[str]]) -> str
    """Format headers and rows into an aligned text table.

    Args:
        headers: List of column header strings.
        rows: List of row lists, each containing column values.

    Returns:
        Formatted table as a string.
    """
    if not rows and not headers:
        return ""

    # Calculate column widths
    col_widths = []  # type: List[int]
    for i, header in enumerate(headers):
        width = len(header)
        for row in rows:
            if i < len(row):
                width = max(width, len(str(row[i])))
        col_widths.append(width)

    # Build formatted table
    lines = []  # type: List[str]

    # Add header
    header_line = ""
    for i, header in enumerate(headers):
        if i > 0:
            header_line += " | "
        header_line += header.ljust(col_widths[i])
    lines.append(header_line)

    # Add separator
    separator = ""
    for i, width in enumerate(col_widths):
        if i > 0:
            separator += "-+-"
        separator += "-" * width
    lines.append(separator)

    # Add rows
    for row in rows:
        row_line = ""
        for i, cell in enumerate(row):
            if i > 0:
                row_line += " | "
            row_line += str(cell).ljust(col_widths[i])
        lines.append(row_line)

    return "\n".join(lines)


def _is_provider_configured(provider):
    # type: (Any) -> bool
    """Check if a provider is configured with necessary credentials.

    This checks if the provider has an API key set in its configuration
    or environment variables.

    Args:
        provider: Provider instance to check.

    Returns:
        True if provider appears to be configured, False otherwise.
    """
    # Check if provider has api_key attribute
    if hasattr(provider, "api_key") and provider.api_key:
        return True

    # Check common environment variables based on provider type
    provider_env_vars = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "azure": "AZURE_OPENAI_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "amazon_bedrock": "AWS_ACCESS_KEY_ID",  # Bedrock uses AWS credentials
        "groq": "GROQ_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "deepinfra": "DEEPINFRA_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
        "together": "TOGETHER_API_KEY",
        "perplexity": "PERPLEXITY_API_KEY",
        "xai": "XAI_API_KEY",
        "venice": "VENICE_API_KEY",
        "gitlab": "GITLAB_TOKEN",
    }

    # Get provider type from class name or ID
    provider_type = None
    if hasattr(provider, "id"):
        # Try to match provider ID to known types
        for known_type in provider_env_vars.keys():
            if known_type in provider.id.lower():
                provider_type = known_type
                break

    if provider_type is None:
        # Fallback: use provider name
        if hasattr(provider, "name"):
            for known_type in provider_env_vars.keys():
                if known_type in provider.name.lower():
                    provider_type = known_type
                    break

    if provider_type and provider_type in provider_env_vars:
        env_var = provider_env_vars[provider_type]
        if os.environ.get(env_var):
            return True

    # If we can't determine the provider type, try to see if it can list models
    # (this will fail if not configured, but we don't want to actually call it here)
    # For now, assume unconfigured if we can't find API key
    return False


def cmd_models(args):
    # type: (Any) -> None
    """List all available models from registered providers.

    Args:
        args: Argument namespace with optional 'provider' attribute.
    """
    provider_filter = getattr(args, "provider", None)

    # Get all providers from registry
    all_providers = []
    for provider_id in registry.list_providers():
        try:
            provider = registry.get(provider_id)
            all_providers.append((provider_id, provider))
        except Exception as e:
            logger.warning("Failed to load provider %s: %s", provider_id, e)
            # Skip providers that can't be loaded
            continue

    # Filter by provider if specified
    if provider_filter:
        filtered_providers = []
        for provider_id, provider in all_providers:
            if provider_id == provider_filter:
                filtered_providers.append((provider_id, provider))
                break
        all_providers = filtered_providers

        if not all_providers:
            print("Provider '{}' not found".format(provider_filter))
            return

    # Collect model data
    rows = []  # type: List[List[str]]
    for provider_id, provider in all_providers:
        try:
            models = provider.list_models()
            for model in models:
                # Try to get context length if available
                context_length = "N/A"
                # Some providers might have model info with context lengths
                # For now, we'll just show N/A since it's not standardized
                rows.append([provider_id, model, context_length])
        except Exception as e:
            logger.warning("Failed to list models for provider %s: %s", provider_id, e)
            # Skip providers that can't list models (likely unconfigured)
            continue

    if not rows:
        if provider_filter:
            print("No models found for provider '{}'".format(provider_filter))
        else:
            print("No models found")
        return

    # Sort rows by provider then model name
    rows.sort(key=lambda x: (x[0], x[1]))

    # Print table
    headers = ["PROVIDER", "MODEL", "CONTEXT LENGTH"]
    print(format_table(headers, rows))


def cmd_providers(args):
    # type: (Any) -> None
    """List all registered providers.

    Args:
        args: Argument namespace (no specific attributes needed).
    """
    provider_ids = registry.list_providers()
    if not provider_ids:
        print("No providers registered")
        return

    rows = []  # type: List[List[str]]
    for provider_id in sorted(provider_ids):
        try:
            provider = registry.get(provider_id)
            status = "configured" if _is_provider_configured(provider) else "unconfigured"
            model_count = len(provider.list_models()) if hasattr(provider, "list_models") else 0
            rows.append([provider_id, provider.name, status, str(model_count)])
        except Exception as e:
            logger.warning("Failed to load provider %s: %s", provider_id, e)
            # Skip providers that can't be loaded
            continue

    if not rows:
        print("No providers available")
        return

    # Print table
    headers = ["PROVIDER ID", "NAME", "STATUS", "MODEL COUNT"]
    print(format_table(headers, rows))
