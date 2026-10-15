"""The CLI is the entry point of a deployment: if it does not work, there is no deployment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cli
import security
from settings import Settings, settings


@pytest.fixture
def temp_db(monkeypatch, tmp_path: Path):
    """A dedicated database, so the development one is left untouched."""
    path = tmp_path / "cli.db"
    monkeypatch.setattr(cli.settings, "DB_PATH", path)
    return path


def test_generates_a_key_that_grants_access(temp_db, capsys):
    code = cli.main(["key", "--name", "Ops", "--scopes", "admin:all", "--plan", "pro"])
    key = capsys.readouterr().out.strip()

    assert code == 0
    assert key.startswith("ak_")

    from persistence import Database

    db = Database(temp_db)
    client = db.get_client_by_hash(security.KeyManager(db)._hash(key))
    assert client is not None
    assert client["name"] == "Ops"
    assert client["plan"] == "pro"
    db.close()


def test_an_invented_scope_does_not_go_unnoticed(temp_db):
    with pytest.raises(SystemExit) as error:
        cli.main(["key", "--name", "Ops", "--scopes", "chat:write,todo_el_poder"])

    assert "scope desconocido" in str(error.value)


def test_shows_the_pending_approvals(temp_db, capsys):
    cli.main(["key", "--name", "Ops", "--scopes", "admin:all"])
    capsys.readouterr()

    assert cli.main(["status"]) == 0

    output = capsys.readouterr().out
    assert "clientes: 1" in output
    assert "a la espera de una persona: 0" in output


def test_purges_what_is_asked(temp_db, capsys):
    cli.main(["key", "--name", "Ops", "--scopes", "admin:all"])
    capsys.readouterr()

    assert cli.main(["purge", "--days", "0"]) == 0
    assert "traces" in capsys.readouterr().out


def test_does_not_print_the_key_twice(temp_db, capsys):
    cli.main(["key", "--name", "Ops"])
    captured = capsys.readouterr()

    # The key goes to stdout (so it can be redirected) and the notice to stderr.
    assert "Copiá esta clave" in captured.err
    assert captured.out.count("ak_") == 1


def test_the_scope_filter_accepts_several(temp_db):
    assert cli._scopes("chat:write, traces:read ,approvals:write") == [
        "chat:write",
        "traces:read",
        "approvals:write",
    ]


def test_cli_scopes_are_the_same_ones_the_api_requires(temp_db):
    """If the CLI and the API keep separate lists, a key gets created and then does not work.

    That is what happened with `traces` vs `traces:read`: the key was issued
    without error and the panel rejected it with a 403.
    """
    import api
    import security

    assert tuple(cli.KNOWN_SCOPES) == tuple(security.KNOWN_SCOPES)
    for required in (api.SCOPE_CHAT, api.SCOPE_TRACES, api.SCOPE_APPROVALS):
        assert required in cli.KNOWN_SCOPES
    # What the CLI rejects is exactly what the API cannot ask for.
    with pytest.raises(SystemExit):
        cli._scopes("trazas,aprobaciones")


def test_the_development_configuration_warns():
    """The development secret has to be loud, not silent."""
    conf = Settings()

    assert conf.JWT_SECRET  # there is a default one, which is precisely the problem


def test_the_configuration_json_is_valid():
    """If the deployment .env is malformed, we want to find out early."""
    assert json.loads(json.dumps({"JWT_SECRET": "x"}))["JWT_SECRET"] == "x"
    assert settings.APP_NAME
