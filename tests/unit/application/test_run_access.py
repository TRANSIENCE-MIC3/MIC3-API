from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest

from mic3_api.application.runs.admission import RunAdmissionExceeded, RunAdmissionLimits
from mic3_api.application.runs.queries import GetRun, ListOwnRuns, RunAccessDenied, RunDetail, RunNotFound
from mic3_api.application.runs.submission import SubmitOwnRun
from mic3_api.application.users import CurrentUser


def test_owner_admin_and_missing_run():
    owner = CurrentUser(uuid4(), None, None, ("member",))
    other = replace(owner, id=uuid4())
    run = RunDetail(uuid4(), uuid4(), owner.id, {"mode": "buildings"}, "queued",
                    datetime.now(timezone.utc), None, None, None, None, ())
    store = Mock()
    store.find.return_value = run
    assert GetRun().execute(owner, store, run.id) == run
    with pytest.raises(RunAccessDenied):
        GetRun().execute(other, store, run.id)
    assert GetRun().execute(replace(other, roles=("admin",)), store, run.id) == run
    store.find.return_value = replace(run, requested_by=None)
    with pytest.raises(RunAccessDenied):
        GetRun().execute(owner, store, run.id)
    store.find.return_value = None
    with pytest.raises(RunNotFound):
        GetRun().execute(owner, store, run.id)


def test_submission_and_listing_use_current_identity_even_for_admin():
    user = CurrentUser(uuid4(), None, None, ("admin",))
    submitter, queries = Mock(), Mock()
    release_id = uuid4()
    SubmitOwnRun(submitter).execute(user, release_id, {"mode": "buildings"})
    submitter.execute.assert_called_once_with(release_id, {"mode": "buildings"}, requested_by=user.id)
    ListOwnRuns().execute(user, queries, limit=10, offset=20)
    queries.list_owned.assert_called_once_with(user.id, 10, 20)
    with pytest.raises(ValueError):
        ListOwnRuns().execute(user, queries, limit=101, offset=0)


def test_admission_boundaries():
    limits = RunAdmissionLimits(5, 20)
    limits.check(4, 19)
    for user_count, total_count in [(5, 5), (0, 20), (6, 21)]:
        with pytest.raises(RunAdmissionExceeded):
            limits.check(user_count, total_count)
    with pytest.raises(ValueError):
        RunAdmissionLimits(0, 20)


@pytest.mark.parametrize("field", ["run_max_active_per_user", "run_max_active_global"])
def test_invalid_admission_settings(test_settings, field):
    from pydantic import ValidationError
    from mic3_api.core.config import Settings
    with pytest.raises(ValidationError):
        Settings(**(test_settings.model_dump() | {field: 0}))
