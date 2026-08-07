"""
berserker.agent.compaction — Configurable compaction strategy module.

Provides:
    CompactionConfig: Dataclass for compaction/pruning configuration.
    CompactionStrategy: Strategy class for compaction decisions.
    default_config(): Returns a CompactionConfig with default values.
    load_compaction_config(): Extracts compaction config from a config dict.

Python 3.8.10 compatible.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


@dataclass
class CompactionConfig:
    """Configuration for context compaction and pruning behavior.

    Attributes:
        auto: Whether to automatically trigger compaction.
        prune: Whether to enable pruning of tool call content.
        reserved: Reserved token buffer (tokens to leave free for next response).
        prune_protect: Protect last N tokens of tool call content from pruning.
        prune_minimum: Minimum total prunable tokens before pruning activates.
        compact_threshold: Fraction of context window that triggers compaction.
    """

    auto: bool = True
    prune: bool = True
    reserved: int = 20000
    prune_protect: int = 40000
    prune_minimum: int = 20000
    compact_threshold: float = 0.8

    _KNOWN_KEYS = frozenset(
        {
            "auto",
            "prune",
            "reserved",
            "prune_protect",
            "prune_minimum",
            "compact_threshold",
        }
    )

    @classmethod
    def from_dict(cls, data):
        # type: (Dict[str, Any]) -> CompactionConfig
        """Create a CompactionConfig from a dict, using defaults for missing keys.

        Only accepts known keys (auto, prune, reserved, prune_protect,
        prune_minimum, compact_threshold). Unknown keys are silently ignored.

        Args:
            data: Dict with config values.

        Returns:
            A new CompactionConfig instance.
        """
        if not isinstance(data, dict):
            return cls()

        filtered = {k: v for k, v in data.items() if k in cls._KNOWN_KEYS}
        return cls(**filtered)

    def to_dict(self):
        # type: () -> Dict[str, Any]
        """Return the config as a dict.

        Returns:
            Dict representation of this config.
        """
        return asdict(self)


class CompactionStrategy:
    """Strategy class that makes compaction and pruning decisions.

    Uses a CompactionConfig to determine when to compact or prune
    context based on token usage.
    """

    def __init__(self, config=None):
        # type: (Optional[CompactionConfig]) -> None
        """Initialize with a CompactionConfig, or use defaults if None.

        Args:
            config: CompactionConfig instance, or None for defaults.
        """
        self.config = config if config is not None else CompactionConfig()

    def should_compact(self, tokens, model_limit):
        # type: (int, int) -> bool
        """Return True if compaction should trigger.

        Compaction triggers when used tokens exceed (model_limit - reserved_buffer).

        Args:
            tokens: Current token count in use.
            model_limit: Maximum token limit for the model's context window.

        Returns:
            True if compaction should be triggered.
        """
        return tokens > model_limit - self.config.reserved

    def should_prune(self, total_prunable_tokens):
        # type: (int) -> bool
        """Return True if pruning should activate.

        Pruning activates when pruning is enabled AND total prunable tokens
        exceed the configured minimum.

        Args:
            total_prunable_tokens: Total number of tokens that could be pruned.

        Returns:
            True if pruning should be performed.
        """
        return self.config.prune and total_prunable_tokens > self.config.prune_minimum

    def get_compact_threshold(self, model_limit):
        # type: (int) -> int
        """Return the token count at which compaction should trigger.

        Calculated as int(model_limit * compact_threshold).

        Args:
            model_limit: Maximum token limit for the model's context window.

        Returns:
            Token threshold for compaction.
        """
        return int(model_limit * self.config.compact_threshold)

    def get_prune_protect(self):
        # type: () -> int
        """Return the number of tokens to protect from pruning.

        Returns:
            prune_protect value from config.
        """
        return self.config.prune_protect


def default_config():
    # type: () -> CompactionConfig
    """Return a CompactionConfig with default values.

    Returns:
        A new CompactionConfig instance with all default settings.
    """
    return CompactionConfig()


def load_compaction_config(config_dict):
    # type: (Optional[Dict[str, Any]]) -> CompactionConfig
    """Extract the compaction key from a config dict and return a CompactionConfig.

    If no compaction key exists, or config_dict is None, returns default_config().

    Args:
        config_dict: Full config dict (may contain a 'compaction' key), or None.

    Returns:
        CompactionConfig instance from the compaction section, or defaults.
    """
    if config_dict is None:
        return default_config()

    compaction_data = config_dict.get("compaction")
    if compaction_data is None:
        return default_config()

    return CompactionConfig.from_dict(compaction_data)
