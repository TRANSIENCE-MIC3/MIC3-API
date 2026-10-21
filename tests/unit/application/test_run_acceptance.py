from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from mic3_api.application.runs.acceptance import AcceptRun, RunForAcceptance


@pytest.mark.parametrize("status,accepted", [
    ("queued", True), ("running", False), ("running", True),
    ("failed", False), ("failed", True), ("succeeded", True), ("succeeded", False),
])
def test_already_accepted_or_ineligible_runs_never_reload_or_change_catalog(status, accepted):
    run = RunForAcceptance(uuid4(), {"mode": "old"}, "a" * 64, status,
                           datetime.now(timezone.utc) if accepted else None, None)
    transaction = Mock(run=run)

    @contextmanager
    def begin(run_id):
        assert run_id == run.id
        yield transaction

    assert AcceptRun(SimpleNamespace(transaction=begin)).execute(run.id) == run
    transaction.load_release.assert_not_called()
    transaction.save_outcome.assert_not_called()
