from unittest.mock import Mock
from uuid import uuid4

from botocore.exceptions import EndpointConnectionError
import pytest

from mic3_api.application.models.catalog import ReleaseDefinition
from mic3_api.application.models.execution import ExecutionDefinition, Invocation
from mic3_api.application.models.outputs import OutputRequirements
from mic3_api.application.runs.artifacts import VerifyOutputs
from mic3_api.application.runs.execution import Artifact, ExecutionFailure, ExecutionWork, InfrastructureUnavailable
from mic3_api.infrastructure.s3_artifacts import S3ArtifactInventory


def work(required=()):
    invocation = Invocation(('command',), '/model', '/outputs', OutputRequirements(required))
    release = ReleaseDefinition('test', 'Test', 'image@sha256:'+'a'*64, ExecutionDefinition({'test':invocation}))
    return ExecutionWork(uuid4(), release, {'mode':'test'}, 'a'*64, 'test', 'uid')


@pytest.mark.parametrize('required,objects,code', [
    ((), [], 'outputs_empty'),
    ((), [('empty',0)], 'outputs_empty'),
    (('required.csv',), [('other.csv',10)], 'required_outputs_missing'),
    (('required.csv',), [('required.csv',0),('other',10)], 'required_outputs_missing'),
])
def test_output_contract_failure_preserves_partial_inventory(required, objects, code):
    item = work(required)
    artifacts = tuple(Artifact('bucket',item.output_prefix+k,n) for k,n in objects)
    with pytest.raises(ExecutionFailure) as result:
        VerifyOutputs(Mock(list=Mock(return_value=artifacts))).collect(item)
    assert result.value.code == code
    assert result.value.artifacts == artifacts


def test_optional_contract_accepts_nonempty_outputs_and_keeps_all_sizes():
    item = work()
    artifacts = (Artifact('bucket',item.output_prefix+'one',10),Artifact('bucket',item.output_prefix+'empty',0))
    assert VerifyOutputs(Mock(list=Mock(return_value=artifacts))).collect(item) == artifacts


def test_inventory_paginates_and_does_not_treat_etag_as_checksum():
    client = Mock()
    client.get_paginator.return_value.paginate.return_value = [
        {'Contents':[{'Key':'prefix/a','Size':12,'ETag':'not-a-checksum'}]},
        {}, {'Contents':[{'Key':'prefix/b','Size':0}]}]
    assert S3ArtifactInventory(client,'bucket').list('prefix/') == (Artifact('bucket','prefix/a',12),Artifact('bucket','prefix/b',0))


def test_storage_outage_is_recoverable():
    client = Mock()
    client.get_paginator.return_value.paginate.side_effect = EndpointConnectionError(endpoint_url='private')
    with pytest.raises(InfrastructureUnavailable):
        S3ArtifactInventory(client,'bucket').list('prefix/')
