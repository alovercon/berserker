import os
from typing import Optional
from berserker.provider.openai_compatible import OpenAICompatibleProvider

MODELS = ["mistral-large", "llama-3.1-70b", "deepseek-coder"]


class GitlabProvider(OpenAICompatibleProvider):
    def __init__(self, api_key=None, timeout=60, max_retries=3):
        # type: (Optional[str], int, int) -> None
        if api_key is None:
            api_key = os.environ.get("GITLAB_API_KEY")
        super(GitlabProvider, self).__init__(
            id="gitlab",
            name="GitLab",
            base_url="https://gitlab.com/api/v4/ai",
            api_key=api_key,
            models=MODELS,
            timeout=timeout,
            max_retries=max_retries,
        )
