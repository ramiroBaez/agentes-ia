"""Shared fixtures for the Project 12 tests.

Each test runs against a temporary SQLite database: the real one (agent.db) is
never touched from a test.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def db(tmp_path: Path):
    """A fresh, empty database per test (never the real agent.db)."""
    from persistence import Database

    base = Database(tmp_path / "agent.db")
    with base:
        base.init()
    return base
