import os
from typing import Optional
from berserker.provider.openai_compatible import OpenAICompatibleProvider

MODELS = ["llama-3.3-70b", "mistral-large", "deepseek-r1"]


class VeniceProvider(OpenAICompatibleProvider):
    def __init__(self, api_key=None, timeout=60, max_retries=3):
        # type: (Optional[str], int, int) -> None
        if api_key is None:
            api_key = os.environ.get("VENICE_API_KEY")
        super(VeniceProvider, self).__init__(
            id="venice",
            name="Venice",
            base_url="https://api.venice.ai/api/v1",
            api_key=api_key,
            models=MODELS,
            timeout=timeout,
            max_retries=max_retries,
        )
