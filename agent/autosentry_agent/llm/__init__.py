"""The chat model behind every agent: one OpenAI-compatible client.

The spec's provider-agnostic `{endpoint, api_key, model}` gateway: OpenAI,
Groq, Gemini (Google's OpenAI-compatible endpoint), Anthropic and local
servers all speak this protocol, so changing provider is configuration.
"""

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from ..config import LLMSettings
from ..secrets.base import SecretsProvider


class LLMConfigError(RuntimeError):
    """The chat model can't be built: no API key is configured."""


def build_chat_model(settings: LLMSettings, secrets: SecretsProvider) -> BaseChatModel:
    try:
        api_key = secrets.get_secret("LLM_API_KEY")
    except KeyError:
        raise LLMConfigError(
            f"no LLM_API_KEY configured for {settings.llm_model} at {settings.llm_base_url}"
        ) from None
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=SecretStr(api_key),
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
