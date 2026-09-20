from __future__ import annotations

import shutil
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'jaffle'


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A throwaway copy of the example dbt project."""
    destination = tmp_path / 'jaffle'
    shutil.copytree(EXAMPLE, destination, ignore=shutil.ignore_patterns('target', 'logs', '*.duckdb'))
    return destination


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'
