from contextlib import nullcontext
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from mic3_api.application.users import CurrentUser
from mic3_api.application.users.administration import AdminRequiredError, ListUsers


def test_member_cannot_open_directory_transaction():
    uow = MagicMock()
    caller = CurrentUser(uuid4(), None, None, ("member",))
    with pytest.raises(AdminRequiredError):
        ListUsers().execute(caller, uow)
    uow.transaction.assert_not_called()
    uow.users.list_users.assert_not_called()


def test_admin_reads_with_explicit_transaction():
    uow = MagicMock()
    uow.transaction.return_value = nullcontext()
    uow.users.list_users.return_value = []
    caller = CurrentUser(uuid4(), None, None, ("admin", "member"))
    assert ListUsers().execute(caller, uow, limit=10, offset=20) == []
    uow.transaction.assert_called_once()
    uow.users.list_users.assert_called_once_with(10, 20)
