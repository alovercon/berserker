import os
from typing import Optional
from berserker.provider.openai_compatible import OpenAICompatibleProvider

MODELS = [
    "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "google/gemma-2b-it",
]


class TogetherProvider(OpenAICompatibleProvider):
    def __init__(self, api_key=None, timeout=60, max_retries=3):
        # type: (Optional[str], int, int) -> None
        if api_key is None:
            api_key = os.environ.get("TOGETHER_API_KEY")
        super(TogetherProvider, self).__init__(
            id="together",
            name="Together",
            base_url="https://api.together.xyz/v1",
            api_key=api_key,
            models=MODELS,
            timeout=timeout,
            max_retries=max_retries,
        )
