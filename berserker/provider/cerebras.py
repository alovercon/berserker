import os
from typing import Optional
from berserker.provider.openai_compatible import OpenAICompatibleProvider

MODELS = ["llama3.1-8b", "llama3.1-70b"]


class CerebrasProvider(OpenAICompatibleProvider):
    def __init__(self, api_key=None, timeout=60, max_retries=3):
        # type: (Optional[str], int, int) -> None
        if api_key is None:
            api_key = os.environ.get("CEREBRAS_API_KEY")
        super(CerebrasProvider, self).__init__(
            id="cerebras",
            name="Cerebras",
            base_url="https://api.cerebras.ai/v1",
            api_key=api_key,
            models=MODELS,
            timeout=timeout,
            max_retries=max_retries,
        )
