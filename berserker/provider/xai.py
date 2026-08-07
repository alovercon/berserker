import os
from typing import Optional
from berserker.provider.openai_compatible import OpenAICompatibleProvider

MODELS = ["grok-beta", "grok-vision-beta"]


class XaiProvider(OpenAICompatibleProvider):
    def __init__(self, api_key=None, timeout=60, max_retries=3):
        # type: (Optional[str], int, int) -> None
        if api_key is None:
            api_key = os.environ.get("XAI_API_KEY")
        super(XaiProvider, self).__init__(
            id="xai",
            name="xAI",
            base_url="https://api.x.ai/v1",
            api_key=api_key,
            models=MODELS,
            timeout=timeout,
            max_retries=max_retries,
        )
