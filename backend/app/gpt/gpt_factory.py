from openai import OpenAI

from app.gpt.base import GPT
from app.gpt.provider.OpenAI_compatible_provider import OpenAICompatibleProvider
from app.gpt.universal_gpt import UniversalGPT
from app.models.model_config import ModelConfig
from app.utils.logger import get_logger


class GPTFactory:
    @staticmethod
    def from_config(config: ModelConfig) -> GPT:
        get_logger("app.gpt.universal_gpt")
        client = OpenAICompatibleProvider(api_key=config.api_key, base_url=config.base_url).get_client
        return UniversalGPT(client=client, model=config.model_name)
