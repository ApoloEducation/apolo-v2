"""The chat model the agent runs on, picked from the environment.

LLM_PROVIDER=openrouter -> OpenRouter (OPENROUTER_MODEL, OPEN_ROUTER_API_KEY); anything else -> Ollama
(OLLAMA_MODEL at OLLAMA_URL, through its OpenAI-compatible /v1 endpoint). Both use native tool calling.
The theme classifier is not here: it calls a decision-model API, not a chat model (see classifier.py).
"""
import os
from functools import lru_cache

import pydantic_ai
from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ModelSettings

pydantic_ai.BANNER_ENABLED = False  # the service owns its stdout/logs
OPENROUTER_URL = "https://openrouter.ai/api/v1"


def describe() -> str:
    """`provider:model`, for logs and benchmark labels."""
    if os.getenv("LLM_PROVIDER") == "openrouter":
        return f"openrouter:{os.getenv('OPENROUTER_MODEL', '')}"
    return f"ollama:{os.getenv('OLLAMA_MODEL', 'qwen2.5:3b')}"


def timeout() -> float:
    return float(os.getenv("OLLAMA_TIMEOUT", "90"))


def settings() -> ModelSettings:
    result: ModelSettings = {"temperature": 0, "timeout": timeout()}
    think = os.getenv("OLLAMA_THINK")
    if think and os.getenv("LLM_PROVIDER") != "openrouter":
        # Reasoning models (qwen3): "1"/"true" enables thinking, "0"/"false" disables it. Sent as Ollama's `think` field.
        result["extra_body"] = {"think": think.lower() in ("1", "true", "yes")}
    return result


def _profile(base: ModelProfile) -> ModelProfile:
    # Apolo never sends mid-conversation system prompts, so Pydantic AI's English note explaining its <system>
    # tag would only be noise ahead of our prompt for small models.
    return {**base, "supports_inline_system_prompts": True}


def _client(base_url: str, api_key: str) -> AsyncOpenAI:
    # No SDK retries: a down or hung model must hand off to a human after one timeout, not three.
    return AsyncOpenAI(base_url=base_url, api_key=api_key or "unset", max_retries=0, timeout=timeout())


@lru_cache
def build_model() -> Model:
    """Built once per process; the environment is read on the first call."""
    if os.getenv("LLM_PROVIDER") == "openrouter":
        provider = OpenRouterProvider(openai_client=_client(OPENROUTER_URL, os.getenv("OPEN_ROUTER_API_KEY", "")))
        return OpenAIChatModel(os.getenv("OPENROUTER_MODEL", ""), provider=provider, profile=_profile, settings=settings())
    base_url = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/") + "/v1"
    provider = OllamaProvider(openai_client=_client(base_url, "ollama"))
    return OpenAIChatModel(os.getenv("OLLAMA_MODEL", "qwen2.5:3b"), provider=provider, profile=_profile, settings=settings())
