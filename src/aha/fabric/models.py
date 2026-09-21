"""Resolving a model name, including OpenAI-compatible endpoints.

Pydantic AI already understands `provider:model` for the providers it ships.
This adds the two cases it cannot infer: opencode Go, and any other endpoint
that speaks the OpenAI chat-completions protocol -- a self-hosted vLLM, a
LiteLLM router, an AWS Bedrock gateway.

Everything else falls through untouched, and falls through as a *string*, so an
agent can still be assembled and inspected without provider credentials.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

OPENCODE_PREFIX = 'opencode-go/'
OPENCODE_BASE_URL = 'https://opencode.ai/zen/go/v1'
OPENCODE_API_KEY_ENV = 'OPENCODE_API_KEY'

COMPATIBLE_PREFIX = 'openai-compatible/'
COMPATIBLE_BASE_URL_ENV = 'AHA_OPENAI_BASE_URL'
COMPATIBLE_API_KEY_ENV = 'AHA_OPENAI_API_KEY'

SESSION_HEADER = 'x-opencode-session'


def _user_agent() -> str:
    """opencode Go asks clients to identify themselves rather than look like an SDK."""
    try:
        return f'aha/{version("aha")}'
    except PackageNotFoundError:
        return 'aha/0'


class ModelConfigurationError(RuntimeError):
    """Raised when a model name needs configuration the environment has not supplied."""


@dataclass(frozen=True, kw_only=True)
class Endpoint:
    """An OpenAI-compatible endpoint and where its settings come from."""

    prefix: str
    base_url: str | None
    api_key_env: str
    base_url_env: str | None = None
    session_header: str | None = None
    """Header carrying a stable per-conversation id, when the endpoint routes on one."""

    def headers(self, session_id: str) -> dict[str, str] | None:
        if self.session_header is None:
            return None
        return {self.session_header: session_id, 'User-Agent': _user_agent()}

    def resolve(self, model_name: str, *, session_id: str) -> Model:
        base_url = self.base_url or os.getenv(self.base_url_env or '')
        if not base_url:
            raise ModelConfigurationError(f'{self.prefix}* needs a base URL. Set {self.base_url_env}.')
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise ModelConfigurationError(f'{self.prefix}{model_name} needs an API key. Set {self.api_key_env}.')
        headers = self.headers(session_id)
        if headers is None:
            provider = OpenAIProvider(base_url=base_url, api_key=api_key)
        else:
            provider = OpenAIProvider(
                openai_client=AsyncOpenAI(
                    base_url=base_url,
                    api_key=api_key,
                    default_headers=headers,
                )
            )
        return OpenAIChatModel(model_name, provider=provider)


ENDPOINTS = (
    Endpoint(
        prefix=OPENCODE_PREFIX,
        base_url=OPENCODE_BASE_URL,
        api_key_env=OPENCODE_API_KEY_ENV,
        session_header=SESSION_HEADER,
    ),
    Endpoint(
        prefix=COMPATIBLE_PREFIX,
        base_url=None,
        base_url_env=COMPATIBLE_BASE_URL_ENV,
        api_key_env=COMPATIBLE_API_KEY_ENV,
    ),
)


def resolve(model: str, *, session_id: str | None = None) -> Model | str:
    """Turn a model name into something `Agent` accepts.

    Known OpenAI-compatible prefixes become a configured `Model`; anything else
    is handed back as a string for Pydantic AI to infer at request time.

    `session_id` should be stable for one conversation -- the run id is ideal.
    Endpoints that route on it use it for prompt caching.
    """
    session_id = session_id or uuid.uuid4().hex
    for endpoint in ENDPOINTS:
        if model.startswith(endpoint.prefix):
            return endpoint.resolve(model.removeprefix(endpoint.prefix), session_id=session_id)
    return model


def describe(model: str) -> str:
    """One line explaining how a model name will be routed, for `aha doctor`."""
    for endpoint in ENDPOINTS:
        if model.startswith(endpoint.prefix):
            target = endpoint.base_url or os.getenv(endpoint.base_url_env or '') or 'unset'
            configured = 'set' if os.getenv(endpoint.api_key_env) else 'missing'
            return f'{model} -> OpenAI-compatible at {target} ({endpoint.api_key_env}: {configured})'
    return f'{model} -> inferred by Pydantic AI'
