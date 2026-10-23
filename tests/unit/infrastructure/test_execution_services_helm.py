"""Deployment contracts rendered with the actual Helm CLI."""
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "deploy/helm/execution-services"
pytestmark = pytest.mark.skipif(not shutil.which("helm"), reason="Helm required")
DIGEST = "example.test/service@sha256:" + "a" * 64


def render(*args):
    result = subprocess.run(["helm", "template", "execution", str(CHART), "-n", "sandbox", *args],
                            capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def test_disabled_defaults_and_security_boundaries():
    docs = render()
    assert not {"Secret", "ClusterRole", "ClusterRoleBinding", "Service"} & {d['kind'] for d in docs}
    accounts = {d['metadata']['name']: d for d in docs if d['kind'] == 'ServiceAccount'}
    for deployment in [d for d in docs if d['kind'] == 'Deployment']:
        name = deployment['metadata']['name']
        worker = name == 'mic3-worker'
        assert deployment['spec']['replicas'] == 0
        assert deployment['spec']['strategy']['type'] == 'Recreate'
        pod = deployment['spec']['template']['spec']
        assert pod['serviceAccountName'] == name
        assert pod['automountServiceAccountToken'] == accounts[name]['automountServiceAccountToken'] == worker
        assert pod['terminationGracePeriodSeconds'] == 60
        assert pod['securityContext'] == {'runAsNonRoot': True, 'seccompProfile': {'type':'RuntimeDefault'}}
        c = pod['containers'][0]
        assert c['securityContext'] == {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False, 'capabilities': {'drop':['ALL']}}
        assert not {'livenessProbe','readinessProbe','ports'} & c.keys()
        assert c.get('args', []) == (['--execute'] if worker else [])
        for kind in ['requests','limits']:
            assert set(c['resources'][kind]) == {'cpu','memory','ephemeral-storage'}
        env = {e['name']: e for e in c['env']}
        assert env['KAFKA_SECURITY_PROTOCOL']['value'] == 'SSL'
        assert env['DB_PASSWORD']['valueFrom']['secretKeyRef'] == {'name': name+'-postgres-credentials', 'key':'password'}
        assert env['DB_USER']['valueFrom']['secretKeyRef']['key'] == 'username'
        assert env['DB_NAME']['valueFrom']['secretKeyRef']['key'] == 'database'
        volumes = {v['name']: v for v in pod['volumes']}
        assert volumes['identity']['secret']['secretName'] == name.replace('mic3-', 'mic3-kafka-')+'-tls'
        assert volumes['trust']['configMap']['name'] == 'mic3-kafka-trust'
        assert 'emptyDir' in volumes['temporary']
        assert 'EXECUTION_KUBECONFIG' not in env and 'EXECUTION_CONTEXT' not in env
        if worker:
            assert env['EXECUTION_CAPACITY']['value'] == '1'
            assert env['ARTIFACT_ACCESS_KEY']['valueFrom']['secretKeyRef']['name'] == 'mic3-artifact-reader-credentials'
        else:
            assert not any(e.startswith('ARTIFACT_') for e in env)


def test_rbac_is_exactly_the_current_adapter_contract():
    docs = render()
    role = next(d for d in docs if d['kind'] == 'Role')
    assert role['rules'] == [{'apiGroups':['batch'],'resources':['jobs'],'verbs':['get','create']},
                             {'apiGroups':[''],'resources':['pods'],'verbs':['list']}]
    binding = next(d for d in docs if d['kind'] == 'RoleBinding')
    assert binding['subjects'] == [{'kind':'ServiceAccount','name':'mic3-worker','namespace':'sandbox'}]


@pytest.mark.parametrize('override', ['publisher.image=example:latest','worker.image=example@sha256:123',
    'worker.replicas=2','worker.capacity=2','publisher.replicas=1','worker.replicas=1',
    'podIdentity.runAsUser=0','modelValues.model.command={bad}',
    'worker.databaseSecret=mic3-publisher-postgres-credentials','kafka.securityProtocol=PLAINTEXT',
    'worker.tlsSecret=mic3-kafka-publisher-tls','artifactReaderSecret=mic3-artifact-uploader-credentials',
    'kafka.eventsTopic=mic3.run.requests.v1'])
def test_unsafe_or_incomplete_values_fail(override):
    with pytest.raises(subprocess.CalledProcessError):
        render('--set', override)


def test_enabled_images_network_destinations_and_model_overrides(tmp_path):
    values = {'publisher':{'replicas':1,'image':DIGEST},'worker':{'replicas':1,'image':DIGEST},
              'networkPolicy':{'apiPeers':[{'ipBlock':{'cidr':'10.0.0.1/32'}}]},
              'modelValues':{'activeDeadlineSeconds':123,'storage':{'bucket':'test-artifacts'}}}
    path = tmp_path/'values.yaml'; path.write_text(yaml.safe_dump(values))
    docs = render('-f', str(path))
    for d in docs:
        if d['kind'] == 'Deployment':
            assert d['spec']['replicas'] == 1
            assert d['spec']['template']['spec']['containers'][0]['image'] == DIGEST
    policies = {d['metadata']['name']:d['spec'] for d in docs if d['kind'] == 'NetworkPolicy'}
    assert len(policies['mic3-publisher']['egress']) == 3
    assert len(policies['mic3-worker']['egress']) == 5
    assert policies['mic3-worker']['egress'][-1]['to'] == values['networkPolicy']['apiPeers']
    cm = next(d for d in docs if d['kind'] == 'ConfigMap')
    operational = yaml.safe_load(cm['data']['values.yaml'])
    assert operational['activeDeadlineSeconds'] == 123
    assert set(operational['model']) == {'resources'}
    path.write_text(cm['data']['values.yaml'])
    invocation = {'runId':'12345678-1234-1234-1234-123456789abc','runFingerprint':'a'*64,
                  'model':{'id':'check','mode':'test','image':DIGEST,'command':['python','-c','pass'],
                           'workingDir':'/tmp','outputPath':'/outputs'}}
    catalog = tmp_path/'catalog.yaml'; catalog.write_text(yaml.safe_dump(invocation))
    result = subprocess.check_output(['helm','template','check',str(ROOT/'deploy/helm/model-run'),
                                     '-f',str(path),'-f',str(catalog)], text=True)
    job = next(d for d in yaml.safe_load_all(result) if d and d['kind']=='Job')
    assert job['spec']['activeDeadlineSeconds'] == 123
    model = job['spec']['template']['spec']['initContainers'][0]
    assert model['image'] == DIGEST and model['command'] == invocation['model']['command']
