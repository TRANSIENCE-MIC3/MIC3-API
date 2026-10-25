"""Delivery gates exercised without cluster mutation or credentials."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('delivery', ROOT/'scripts/execution_delivery.py')
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)
IMAGE = 'example.test/service@sha256:' + 'a'*64
COMMIT = 'c'*40


def deployment(role='worker'):
    return {'metadata': {'generation': 3, 'annotations': {
        delivery.ANNOTATION+'version': '0.1.9', delivery.ANNOTATION+'commit': COMMIT}},
        'spec': {'replicas': 1, 'template': {'spec': {'containers': [{'name': role, 'image': IMAGE}]}}},
        'status': {'observedGeneration': 3, 'updatedReplicas': 1, 'readyReplicas': 1,
                   'availableReplicas': 1, 'replicas': 1}}


def test_same_version_retry_and_forward_update():
    delivery.check_deployment(deployment(), 'worker', '0.1.9', IMAGE, COMMIT, deployed=True)
    delivery.check_deployment(deployment(), 'worker', '0.1.10', IMAGE, 'd'*40)


@pytest.mark.parametrize('defect', ['older', 'digest', 'commit', 'missing', 'paused', 'scaled',
                                  'stale', 'partial', 'old-pod'])
def test_bad_or_incomplete_live_release_is_refused(defect):
    doc = deployment()
    target = '0.1.9'
    if defect == 'older': target = '0.1.8'
    if defect == 'digest': doc['spec']['template']['spec']['containers'][0]['image'] = 'wrong'
    if defect == 'commit': doc['metadata']['annotations'][delivery.ANNOTATION+'commit'] = 'd'*40
    if defect == 'missing': doc['metadata']['annotations'] = {}
    if defect == 'paused': doc['spec']['replicas'] = 0
    if defect == 'scaled': doc['spec']['replicas'] = 2
    if defect == 'stale': doc['status']['observedGeneration'] = 2
    if defect == 'partial': doc['status']['updatedReplicas'] = 0
    if defect == 'old-pod': doc['status']['replicas'] = 2
    with pytest.raises(ValueError):
        delivery.check_deployment(doc, 'worker', target, IMAGE, COMMIT, deployed=True)


@pytest.mark.parametrize('status', ['pending-install', 'pending-upgrade', 'pending-rollback', 'uninstalled', 'unknown'])
def test_pending_or_missing_helm_state_requires_operator(status):
    with pytest.raises(ValueError): delivery.check_helm_status({'info': {'status': status}})


@pytest.mark.parametrize('status', ['deployed', 'failed'])
def test_terminal_helm_revision_allows_retry(status):
    delivery.check_helm_status({'info': {'status': status}})


def environment():
    return {'EXECUTION_API_CIDRS': '["10.0.0.1/32"]', 'VERSION': '0.1.9', 'COMMIT': COMMIT,
            'IMAGE_REF': IMAGE, 'PUBLISHER_IMAGE_REF': IMAGE, 'WORKER_IMAGE_REF': IMAGE}


@pytest.mark.parametrize('cidrs', ['[]', '"10.0.0.1/32"', '["bad"]', '["10.0.0.1/24"]'])
def test_invalid_cluster_destinations_fail_before_delivery(cidrs):
    with pytest.raises(ValueError): delivery.delivery_values({**environment(), 'EXECUTION_API_CIDRS': cidrs})


def test_diagnostic_job_has_no_token_or_kafka_credentials_and_no_retry():
    values = yaml.safe_load((ROOT/'deploy/helm/execution-services/values.yaml').read_text())
    job = delivery.verification_job(environment(), values)
    pod = job['spec']['template']['spec']
    assert job['spec']['backoffLimit'] == 0 and job['spec']['activeDeadlineSeconds'] == 960
    assert 'ttlSecondsAfterFinished' not in job['spec']  # added after diagnostics only
    assert pod['restartPolicy'] == 'Never' and pod['automountServiceAccountToken'] is False
    container = pod['containers'][0]
    assert container['image'] == IMAGE and container['command'][:2] == ['python', '-c']
    compile(container['command'][2], 'embedded-check', 'exec')
    assert not any(env['name'].startswith('KAFKA') for env in container['env'])
    secrets = {env['valueFrom']['secretKeyRef']['name'] for env in container['env'] if 'valueFrom' in env}
    assert secrets == {'mic3-postgres-credentials', 'mic3-artifact-reader-credentials'}


def test_preflight_requires_existing_resources_and_permissions_before_validation(monkeypatch, tmp_path):
    for key, value in environment().items(): monkeypatch.setenv(key, value)
    (tmp_path/'execution.yaml').write_text('kind: Deployment\nmetadata: {name: mic3-worker}\n')
    calls = []
    monkeypatch.setattr(delivery, 'command', lambda *args: '{"info":{"status":"deployed"}}')
    def oc(*args):
        calls.append(args)
        if args[:2] == ('get', 'deployment'): return json.dumps(deployment(args[2].removeprefix('mic3-')))
        if args[:2] == ('auth', 'can-i'): raise ValueError('Denied')
        return ''
    monkeypatch.setattr(delivery, 'oc', oc)
    with pytest.raises(ValueError, match='Denied'): delivery.preflight(tmp_path)
    assert not any(args[0] in ('apply', 'create') for args in calls)


def test_deployer_rbac_does_not_grant_cluster_or_arbitrary_workload_updates():
    objects = yaml.safe_load((ROOT/'deploy/okd/deployer-template.yaml').read_text())['objects']
    assert all(obj['kind'] not in ('ClusterRole', 'ClusterRoleBinding') for obj in objects)
    rules = next(obj for obj in objects if obj['kind'] == 'Role')['rules']
    for rule in rules:
        assert not {'*', 'bind', 'escalate'} & set(rule['verbs'])
        if 'deployments' in rule['resources']:
            assert set(rule['resourceNames']) == {'mic3-api', 'mic3-publisher', 'mic3-worker'}
        if 'roles' in rule['resources']:
            assert rule['resourceNames'] == ['mic3-worker']
    assert next(rule for rule in rules if rule['resources'] == ['secrets'])['verbs'] == ['get', 'list', 'create', 'update', 'delete']


def test_eosc_values_render_identity_and_verified_network_peers(tmp_path, monkeypatch):
    for key, value in environment().items(): monkeypatch.setenv(key, value)
    monkeypatch.setenv('OPENSHIFT_NAMESPACE', 'sandbox')
    delivery.render(tmp_path)
    docs = list(yaml.safe_load_all((tmp_path/'execution.yaml').read_text()))
    for doc in docs:
        if doc['kind'] == 'Deployment':
            assert doc['spec']['replicas'] == 1
            assert doc['spec']['strategy']['type'] == 'Recreate'
            assert doc['metadata']['annotations'][delivery.ANNOTATION+'commit'] == COMMIT
            assert doc['metadata']['annotations'][delivery.ANNOTATION+'image'] == IMAGE
    policy = next(doc for doc in docs if doc['kind'] == 'NetworkPolicy' and doc['metadata']['name'] == 'mic3-worker')
    peers = policy['spec']['egress'][-1]['to']
    assert {'ipBlock': {'cidr': '10.0.0.1/32'}} in peers
    assert any(peer.get('namespaceSelector', {}).get('matchLabels', {}).get('kubernetes.io/metadata.name')
               == 'openshift-kube-apiserver' and peer['podSelector'] == {} for peer in peers)
