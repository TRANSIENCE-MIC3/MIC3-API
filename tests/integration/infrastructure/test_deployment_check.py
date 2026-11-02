"""Real migrated persistence plus simulated object storage for delivery checks."""
from datetime import datetime, timezone
import importlib.util
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mic3_api.infrastructure.persistence.execution import Run, RunArtifact, OutboxEvent
from mic3_api.infrastructure.persistence.run_inspection import SqlAlchemyRunInspection

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('deployment_check', ROOT/'scripts/deployment_check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class Storage:
    def __init__(self, content=check.EXPECTED): self.content = content; self.calls = []
    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        return {'Body': BytesIO(self.content)}


def test_submit_uses_atomic_application_path_and_distinct_runs(migrated_engine, capsys):
    with Session(migrated_engine) as session:
        first = check.register_and_submit(session, 'example.test/api@sha256:'+'a'*64)
        second = check.register_and_submit(session, 'example.test/api@sha256:'+'a'*64)
        assert first != second
        result = SqlAlchemyRunInspection(session).show(first)
        assert result['status'] == 'queued' and result['requested_by'] is None
        assert [event['event_type'] for event in result['events']] == ['run.requested']
        assert str(first) in capsys.readouterr().out


@pytest.mark.parametrize('defect', [None, 'failed', 'missing', 'bytes', 'unpublished', 'acceptance'])
def test_terminal_verification(migrated_engine, defect):
    with Session(migrated_engine) as session:
        run_id = check.register_and_submit(session, 'example.test/api@sha256:'+'a'*64)
        with session.begin():
            run = session.get(Run, run_id)
            run.status = 'failed' if defect == 'failed' else 'succeeded'
            run.accepted_at = None if defect == 'acceptance' else datetime.now(timezone.utc)
            run.job_uid = 'job-uid'
            if defect != 'missing':
                session.add(RunArtifact(run_id=run_id, bucket='bucket',
                    object_key=f'model/sha256-digest/{run_id}/outputs/result.txt', size_bytes=len(check.EXPECTED)))
            for event in session.scalars(select(OutboxEvent)):
                event.published_at = datetime.now(timezone.utc)
            session.add(OutboxEvent(run_id=run_id, event_type='run.succeeded',
                published_at=None if defect == 'unpublished' else datetime.now(timezone.utc)))
        result = SqlAlchemyRunInspection(session).show(run_id)
        storage = Storage(b'wrong' if defect == 'bytes' else check.EXPECTED)
        if defect in ('failed', 'missing', 'bytes', 'acceptance'):
            with pytest.raises(RuntimeError): check.verify_result(result, storage, 'bucket')
        else:
            assert check.verify_result(result, storage, 'bucket') is (defect is None)


def test_timeout_preserves_queued_run(migrated_engine):
    with Session(migrated_engine) as session:
        run_id = check.register_and_submit(session, 'example.test/api@sha256:'+'a'*64)
        store = SqlAlchemyRunInspection(session)
        times = iter([0, 0, 1, 2])
        with pytest.raises(TimeoutError):
            check.wait_for_run(lambda: store.show(run_id), Storage(), 'bucket', timeout=1,
                               clock=lambda: next(times), sleep=lambda _: None)
        result = store.show(run_id)
        assert result['status'] == 'queued' and len(result['events']) == 1
