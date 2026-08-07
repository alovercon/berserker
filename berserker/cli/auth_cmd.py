"""
Authentication CLI commands for berserker.

Provides commands for managing API keys for different providers:
- auth login: Save an API key for a provider
- auth logout: Remove an API key for a provider
- auth status: Show status of all configured providers
"""

import json
import os
from berserker.paths import get_config_dir


def _get_auth_file_path():
    # type: () -> str
    """Return the path to the auth.json file."""
    config_dir = get_config_dir()
    return os.path.join(config_dir, "auth.json")


def _load_auth_data():
    # type: () -> dict
    """Load authentication data from auth.json file."""
    auth_file = _get_auth_file_path()
    if os.path.exists(auth_file):
        with open(auth_file, "r") as f:
            try:
                return json.load(f)
            except (ValueError, IOError):
                return {}
    return {}


def _save_auth_data(data):
    # type: (dict) -> None
    """Save authentication data to auth.json file."""
    auth_file = _get_auth_file_path()
    config_dir = os.path.dirname(auth_file)
    if not os.path.exists(config_dir):
        os.makedirs(config_dir, exist_ok=True)

    with open(auth_file, "w") as f:
        json.dump(data, f, indent=2)

    # Restrict file permissions to owner-only (read/write)
    try:
        os.chmod(auth_file, 0o600)
    except OSError:
        pass  # Windows may not support Unix permissions


def cmd_auth_login(provider, api_key):
    # type: (str, str) -> None
    """
    Save API key for provider to auth.json.

    Args:
        provider: Provider identifier (e.g., 'openai', 'anthropic')
        api_key: API key for the provider
    """
    auth_data = _load_auth_data()
    auth_data[provider] = api_key
    _save_auth_data(auth_data)
    print("Saved API key for provider '{}'".format(provider))


def cmd_auth_logout(provider):
    # type: (str) -> None
    """
    Remove API key for provider from auth.json.

    Args:
        provider: Provider identifier to remove
    """
    auth_data = _load_auth_data()
    if provider in auth_data:
        del auth_data[provider]
        _save_auth_data(auth_data)
        print("Removed API key for provider '{}'".format(provider))
    else:
        print("No API key found for provider '{}'".format(provider))


def cmd_auth_status():
    # type: () -> None
    """
    List all providers with saved API keys.

    Shows a table with PROVIDER | STATUS where STATUS shows first 4 chars + ***
    if configured, or 'unconfigured' if not.
    """
    auth_data = _load_auth_data()

    if not auth_data:
        print("No providers configured")
        return

    # Print header
    print("{:<20} | {:<15}".format("PROVIDER", "STATUS"))
    print("-" * 38)

    # Sort providers for consistent output
    for provider in sorted(auth_data.keys()):
        api_key = auth_data[provider]
        if api_key and len(api_key) >= 4:
            # Show first 4 characters + ***
            masked_key = api_key[:4] + "***"
            print("{:<20} | {:<15}".format(provider, masked_key))
        else:
            print("{:<20} | {:<15}".format(provider, "unconfigured"))
