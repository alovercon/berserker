import os
from typing import Optional
from berserker.provider.openai_compatible import OpenAICompatibleProvider
MODELS = [
    "openai/gpt-4o",
    "anthropic/claude-3.5-sonnet",
    "google/gemini-pro-1.5",
    "meta-llama/llama-3.1-70b-instruct",
]
class OpenrouterProvider(OpenAICompatibleProvider):
    def __init__(self, api_key=None, timeout=60, max_retries=3):
        # type: (Optional[str], int, int) -> None
        if api_key is None:
            api_key = os.environ.get("OPENROUTER_API_KEY")
        super(OpenrouterProvider, self).__init__(
            id="openrouter", name="OpenRouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key, models=MODELS,
            timeout=timeout, max_retries=max_retries)
