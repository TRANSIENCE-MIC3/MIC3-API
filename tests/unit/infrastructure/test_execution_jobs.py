from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from kubernetes.client.exceptions import ApiException

from mic3_api.application.models.catalog import ReleaseDefinition
from mic3_api.application.models.execution import ExecutionDefinition
from mic3_api.application.runs.execution import ExecutionFailure, ExecutionWork, InfrastructureUnavailable
from mic3_api.application.runs.fingerprint import fingerprint
from mic3_api.infrastructure.job_renderer import HelmJobRenderer
from mic3_api.infrastructure.kubernetes_jobs import KubernetesJobs
from tests.catalog_data import CONFIG, PARAMETERS


@pytest.fixture
def work():
    release = ReleaseDefinition(CONFIG['model_id'], CONFIG['display_name'], CONFIG['image_digest'], ExecutionDefinition.from_dict(CONFIG['execution_definition']))
    return ExecutionWork(uuid4(), release, PARAMETERS, fingerprint(release, PARAMETERS), 'test', None)


@pytest.fixture
def adapter(work):
    expected = HelmJobRenderer(Path('deploy/helm/model-run')).render(work)
    actual = deepcopy(expected)
    actual['metadata']['uid'] = 'job-uid'
    api = Mock()
    api.sanitize_for_serialization.side_effect = lambda x:x
    result = KubernetesJobs(api, Mock(render=Mock(return_value=expected)), 'test')
    result.batch = Mock()
    result.core = Mock()
    result.batch.read_namespaced_job.return_value = actual
    return result, actual


def test_creation_timeout_recovers_same_job(adapter, work):
    jobs, actual = adapter
    jobs.batch.read_namespaced_job.side_effect = [ApiException(status=404), actual]
    jobs.batch.create_namespaced_job.side_effect = TimeoutError()
    assert jobs.observe_or_create(work).uid == 'job-uid'
    jobs.batch.create_namespaced_job.assert_called_once()


def test_ambiguous_missing_creation_is_recoverable(adapter, work):
    jobs, _ = adapter
    jobs.batch.read_namespaced_job.side_effect = ApiException(status=404)
    jobs.batch.create_namespaced_job.side_effect = TimeoutError()
    with pytest.raises(InfrastructureUnavailable):
        jobs.observe_or_create(work)


def test_existing_matching_job_is_adopted(adapter, work):
    jobs, _ = adapter
    assert jobs.observe_or_create(work).state == 'active'
    jobs.batch.create_namespaced_job.assert_not_called()


@pytest.mark.parametrize('change', ['image', 'uid', 'identity'])
def test_mismatched_job_never_replaced(adapter, work, change):
    jobs, actual = adapter
    work = replace(work, job_uid='job-uid')
    if change == 'image':
        actual['spec']['template']['spec']['initContainers'][0]['image'] = 'wrong'
    elif change == 'uid':
        actual['metadata']['uid'] = 'replacement'
    else:
        actual['metadata']['labels']['mic3.io/run-id'] = str(uuid4())
    with pytest.raises(ExecutionFailure, match='job_identity_conflict'):
        jobs.observe_or_create(work)
    jobs.batch.create_namespaced_job.assert_not_called()


def test_recorded_missing_job_is_failure(adapter, work):
    jobs, _ = adapter
    jobs.batch.read_namespaced_job.side_effect = ApiException(status=404)
    with pytest.raises(ExecutionFailure, match='job_lost'):
        jobs.observe_or_create(replace(work, job_uid='job-uid'))
    jobs.batch.create_namespaced_job.assert_not_called()


@pytest.mark.parametrize('container,reason', [('model','model_failed'), ('copy','upload_failed'), ('other','job_failed')])
def test_failure_classification(adapter, work, container, reason):
    jobs, actual = adapter
    actual['status'] = {'conditions':[{'type':'Failed','status':'True'}]}
    jobs.core.list_namespaced_pod.return_value = {'items':[{'metadata':{'ownerReferences':[{'uid':'job-uid'}]},'status':{'containerStatuses':[{'name':container,'state':{'terminated':{'exitCode':1}}}]}}]}
    assert jobs.observe_or_create(work).failure_code == reason


def test_complete_job(adapter, work):
    jobs, actual = adapter
    actual['status'] = {'conditions':[{'type':'Complete','status':'True'}]}
    assert jobs.observe_or_create(work).state == 'succeeded'
