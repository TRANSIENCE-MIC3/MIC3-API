from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from mic3_api import cli
from mic3_api.application.users.administration import AdministrationError


@pytest.mark.parametrize("args", [[], ["grant-admin"], ["grant-admin", "--user-id", "invalid"]])
def test_invalid_arguments(args):
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2


@pytest.mark.parametrize("result", [True, False, AdministrationError("Unknown user"), OperationalError("secret", {}, Exception("secret"))])
def test_cli_cleanup_and_output(monkeypatch, capsys, result):
    database = MagicMock()
    monkeypatch.setattr(cli, "DatabaseSettings", MagicMock())
    monkeypatch.setattr(cli, "Database", MagicMock(return_value=database))
    execute = MagicMock(return_value=result)
    if isinstance(result, Exception):
        execute.side_effect = result
    monkeypatch.setattr(cli.GrantAdmin, "execute", execute)
    user_id = str(uuid4())
    assert cli.main(["grant-admin", "--user-id", user_id]) == (1 if isinstance(result, Exception) else 0)
    database.dispose.assert_called_once()
    database.open_session.return_value.__exit__.assert_called_once()
    output = capsys.readouterr()
    assert "secret" not in output.err
    if not isinstance(result, Exception):
        assert user_id in output.out
        assert ("granted" if result else "already present") in output.out
