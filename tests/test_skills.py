"""The dbt skill library: present, parseable, and pointed at the right cases.

Skills are files rather than code, so nothing else would catch a malformed
frontmatter block until a run tried to load one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai_harness import Skills

from aha.fabric import get

EXPECTED = {'grain-and-joins', 'test-design', 'incremental-models', 'debugging-failures'}
MAX_DESCRIPTION_CHARS = 1024


@pytest.fixture(scope='module')
def loaded() -> dict[str, str]:
    directory = get('dbt').skills()
    assert directory is not None, 'the dbt pack should ship a skill library'
    return {
        capability.id: capability.description or ''
        for capability in Skills(directory)._deferred_capabilities
    }


def test_the_expected_skills_load(loaded: dict[str, str]) -> None:
    assert set(loaded) == EXPECTED


def test_every_skill_describes_when_to_reach_for_it(loaded: dict[str, str]) -> None:
    """A description is the skill's only context pointer -- it decides whether it fires."""
    for name, description in loaded.items():
        assert description, f'{name} has no description'
        assert len(description) <= MAX_DESCRIPTION_CHARS, f'{name} description is too long'
        assert 'Use when' in description, f'{name} does not say when to fire'


def test_skills_are_packaged_with_the_dbt_pack() -> None:
    """They must ship inside the installed package, not sit beside the repo."""
    directory = get('dbt').skills()
    assert directory is not None
    assert directory.parent.parent.name == 'packs'
    assert sorted(p.parent.name for p in directory.glob('*/SKILL.md')) == sorted(EXPECTED)


def test_a_missing_library_is_not_an_error(tmp_path: Path) -> None:
    from aha.fabric.assembly import skill_capabilities
    from aha.fabric.spec import TaskSpec

    spec = TaskSpec(name='t', goal='g', workspace=tmp_path, pack='generic')
    assert skill_capabilities(spec, get('generic')) == []
