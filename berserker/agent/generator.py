"""
LLM-based agent generation for berserker.

Provides:
- AgentGenerationError: Custom exception for generation failures.
- AgentGenerator: Uses LLM to generate agent configurations from natural language.

Python 3.8.10 compatible: uses type comments, typing module imports.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from berserker.agent.exceptions import AgentSchemaValidationError
from berserker.agent.factory import create_agent_from_schema
from berserker.agent.prompts import AGENT_GENERATION_PROMPT, AGENT_VALIDATION_PROMPT
from berserker.agent.registry import AgentRegistry
from berserker.agent.schema import AgentSchema, validate_agent_schema
from berserker.provider.base import ChatMessage, ProviderError

logger = logging.getLogger(__name__)


class AgentGenerationError(Exception):
    """Raised when agent generation fails."""

    def __init__(self, message, cause=None):
        # type: (str, Optional[Exception]) -> None
        super(AgentGenerationError, self).__init__(message)
        self.cause = cause


class AgentGenerator(object):
    """Generates agent configurations from natural language descriptions using LLM.

    This class uses an LLM provider to convert natural language descriptions
    into valid AgentSchema configurations, with validation and optional
    registration into the agent registry.

    Usage:
        generator = AgentGenerator(agent_registry, provider_registry)
        schema = generator.generate("Create a code review agent")
        name = generator.generate_and_register("Create a test writer agent")
    """

    def __init__(self, registry, provider_registry):
        # type: (AgentRegistry, Any) -> None
        """Initialize the agent generator.

        Args:
            registry: AgentRegistry instance for registering generated agents.
            provider_registry: ProviderRegistry instance for LLM provider access.
        """
        self._registry = registry
        self._provider_registry = provider_registry

    def generate(self, description, provider=None, model=None):
        # type: (str, Optional[str], Optional[str]) -> AgentSchema
        """Generate an agent configuration from a natural language description.

        Args:
            description: Natural language description of the desired agent.
            provider: Optional provider ID to use for generation. If None,
                      the default provider is used.
            model: Optional model name to use for generation. If None,
                   a default model is used.

        Returns:
            A validated AgentSchema instance.

        Raises:
            AgentGenerationError: If generation fails (invalid LLM response,
                                  provider error, etc.).
        """
        if not description or not description.strip():
            raise AgentGenerationError("Description cannot be empty")

        # Build the generation prompt
        prompt = AGENT_GENERATION_PROMPT.format(description=description)

        # Call the LLM
        response = self._call_llm(prompt, provider, model)

        # Validate and parse the response
        schema = self._validate_response(response)

        logger.info(
            "Generated agent schema: name='%s', mode='%s', model='%s'",
            schema.name,
            schema.mode,
            schema.model,
        )

        return schema

    def generate_and_register(self, description, provider=None, model=None):
        # type: (str, Optional[str], Optional[str]) -> str
        """Generate an agent configuration and register it.

        Args:
            description: Natural language description of the desired agent.
            provider: Optional provider ID to use for generation.
            model: Optional model name to use for generation.

        Returns:
            The name of the registered agent.

        Raises:
            AgentGenerationError: If generation or registration fails.
        """
        # Generate the schema
        schema = self.generate(description, provider, model)

        # Create the agent instance
        try:
            agent = create_agent_from_schema(schema)
        except AgentSchemaValidationError as exc:
            raise AgentGenerationError(
                "Generated schema is invalid: {}".format(str(exc)),
                cause=exc,
            )

        # Register the agent
        try:
            self._registry.register(agent)
        except Exception as exc:
            raise AgentGenerationError(
                "Failed to register agent '{}': {}".format(schema.name, str(exc)),
                cause=exc,
            )

        logger.info("Generated and registered agent: '%s'", schema.name)
        return schema.name

    def _validate_response(self, response):
        # type: (str) -> AgentSchema
        """Validate an LLM response and convert it to an AgentSchema.

        Args:
            response: Raw string response from the LLM.

        Returns:
            A validated AgentSchema instance.

        Raises:
            AgentGenerationError: If the response is not valid JSON or
                                  does not match the AgentSchema structure.
        """
        if not response or not response.strip():
            raise AgentGenerationError("Empty response from LLM")

        # Try to extract JSON from the response (handle potential markdown fences)
        json_str = response.strip()

        # Remove markdown code fences if present
        if json_str.startswith("```"):
            # Find the first newline after ```
            first_newline = json_str.find("\n")
            if first_newline != -1:
                json_str = json_str[first_newline:]
            # Remove trailing ```
            if json_str.rstrip().endswith("```"):
                json_str = json_str.rstrip()[:-3]
            json_str = json_str.strip()

        # Parse JSON
        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError) as exc:
            raise AgentGenerationError(
                "LLM response is not valid JSON: {}".format(str(exc)),
                cause=exc,
            )

        if not isinstance(data, dict):
            raise AgentGenerationError(
                "LLM response is not a JSON object, got {}".format(type(data).__name__)
            )

        # Validate against AgentSchema
        try:
            schema = validate_agent_schema(data)
        except AgentSchemaValidationError as exc:
            raise AgentGenerationError(
                "Generated agent schema validation failed: {}".format(str(exc)),
                cause=exc,
            )

        return schema

    def _call_llm(self, prompt, provider_id, model):
        # type: (str, Optional[str], Optional[str]) -> str
        """Call the LLM provider to generate a response.

        Args:
            prompt: The prompt string to send to the LLM.
            provider_id: Optional provider ID. If None, uses the first available provider.
            model: Optional model name. If None, uses the provider's default model.

        Returns:
            The raw text response from the LLM.

        Raises:
            AgentGenerationError: If no provider is available or the call fails.
        """
        # Resolve provider
        provider = None  # type: Any
        model_name = model  # type: Optional[str]

        if provider_id is not None:
            try:
                provider = self._provider_registry.get(provider_id)
            except ProviderError as exc:
                raise AgentGenerationError(
                    "Provider '{}' not found: {}".format(provider_id, str(exc)),
                    cause=exc,
                )
        else:
            # Use the first available provider
            provider_ids = self._provider_registry.list_providers()
            if not provider_ids:
                raise AgentGenerationError(
                    "No LLM providers available. Register a provider before generating agents."
                )
            provider = self._provider_registry.get(provider_ids[0])
            if model_name is None and provider.models:
                model_name = provider.models[0]

        if model_name is None:
            model_name = "gpt-4o"

        # Build messages
        messages = [
            ChatMessage(role="user", content=prompt),
        ]

        # Call the provider
        try:
            response = provider.chat(messages, model_name, temperature=0.7)
        except ProviderError as exc:
            raise AgentGenerationError(
                "LLM call failed: {}".format(str(exc)),
                cause=exc,
            )

        if response.content is None or not response.content.strip():
            raise AgentGenerationError("LLM returned empty response")

        return response.content
