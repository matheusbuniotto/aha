"""Model routing, including the OpenAI-compatible endpoints Pydantic AI cannot infer."""

from __future__ import annotations

import pytest
from pydantic_ai.models.openai import OpenAIChatModel

from aha.fabric.models import (
    COMPATIBLE_API_KEY_ENV,
    COMPATIBLE_BASE_URL_ENV,
    OPENCODE_API_KEY_ENV,
    OPENCODE_BASE_URL,
    ModelConfigurationError,
    describe,
    resolve,
)


def test_known_providers_pass_through_as_strings() -> None:
    """Passing the name through is what keeps agent construction credential-free."""
    assert resolve('anthropic:claude-sonnet-4-6') == 'anthropic:claude-sonnet-4-6'
    assert resolve('openai:gpt-5.6-sol') == 'openai:gpt-5.6-sol'


def test_opencode_go_resolves_to_its_openai_compatible_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENCODE_API_KEY_ENV, 'test-key')

    model = resolve('opencode-go/kimi-k3')

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == 'kimi-k3'
    assert str(model.client.base_url).rstrip('/') == OPENCODE_BASE_URL


def test_a_self_hosted_endpoint_is_configured_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(COMPATIBLE_BASE_URL_ENV, 'http://localhost:4000/v1')
    monkeypatch.setenv(COMPATIBLE_API_KEY_ENV, 'test-key')

    model = resolve('openai-compatible/llama-3.3-70b')

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == 'llama-3.3-70b'
    assert str(model.client.base_url).rstrip('/') == 'http://localhost:4000/v1'


def test_a_missing_key_names_the_variable_to_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENCODE_API_KEY_ENV, raising=False)

    with pytest.raises(ModelConfigurationError, match=OPENCODE_API_KEY_ENV):
        resolve('opencode-go/kimi-k3')


def test_a_missing_base_url_names_the_variable_to_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(COMPATIBLE_BASE_URL_ENV, raising=False)

    with pytest.raises(ModelConfigurationError, match=COMPATIBLE_BASE_URL_ENV):
        resolve('openai-compatible/llama-3')


def test_describe_reports_routing_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENCODE_API_KEY_ENV, raising=False)

    assert 'inferred by Pydantic AI' in describe('anthropic:claude-sonnet-4-6')
    assert OPENCODE_BASE_URL in describe('opencode-go/kimi-k3')


@pytest.mark.anyio
async def test_a_misconfigured_model_fails_the_run_instead_of_crashing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator typo should read as a failed run in the journal, not a traceback."""
    from aha.fabric import Autonomy, LocalRunner, TaskSpec, get
    from aha.fabric.journal import Journal

    monkeypatch.delenv(OPENCODE_API_KEY_ENV, raising=False)
    workspace = tmp_path / 'work'
    workspace.mkdir()

    spec = TaskSpec(
        name='misconfigured',
        goal='create a python script',
        workspace=workspace,
        pack='generic',
        autonomy=Autonomy.guarded,
        model='opencode-go/kimi-k3',
        policy=get('generic').policy(),
    )

    outcome = await LocalRunner(journal_path=tmp_path / 'journal.db').run(spec)

    assert outcome.status == 'failed'
    assert OPENCODE_API_KEY_ENV in outcome.output
    errors = [
        payload
        for _, kind, payload in Journal(path=tmp_path / 'journal.db', run_id=outcome.run_id).events()
        if kind == 'run_error'
    ]
    assert errors, 'the failure should be recorded'
