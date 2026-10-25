"""Real Helm SSA ownership regression; disabled workloads in an isolated kind namespace."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlparse
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.smoke, pytest.mark.skipif(
    os.getenv('MIC3_EXECUTION_SERVICES_SMOKE') != '1', reason='Opt-in isolated kind smoke')]


@pytest.mark.parametrize('legacy_owner', [False, True])
def test_bootstrap_then_helm_upgrade(tmp_path, monkeypatch, legacy_owner):
    base = ['kubectl', '--kubeconfig', os.environ['MIC3_KIND_KUBECONFIG'],
            '--context', os.environ['MIC3_KIND_CONTEXT']]
    def command(*args):
        return subprocess.check_output([str(arg) for arg in args], text=True, timeout=60).strip()
    assert os.environ['MIC3_KIND_CONTEXT'].startswith('kind-')
    config = json.loads(command(*base, 'config', 'view', '--minify', '-o', 'json'))
    assert urlparse(config['clusters'][0]['cluster']['server']).hostname in ('127.0.0.1', 'localhost', '::1')
    nodes = json.loads(command(*base, 'get', 'nodes', '-o', 'json'))['items']
    assert nodes and all(n['spec'].get('providerID', '').startswith('kind://') for n in nodes)
    namespace = 'mic3-bootstrap-' + uuid4().hex[:10]
    k = [*base, '-n', namespace]
    helm = ['helm', '--kubeconfig', os.environ['MIC3_KIND_KUBECONFIG'],
            '--kube-context', os.environ['MIC3_KIND_CONTEXT'], '-n', namespace]
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    spec = importlib.util.spec_from_file_location('bootstrap_smoke', ROOT/'scripts/bootstrap_execution_identity.py')
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    monkeypatch.setattr(bootstrap, 'oc', lambda *args: command(*k, *args))
    command(*base, 'create', 'namespace', namespace)
    try:
        chart = ROOT/'deploy/helm/execution-services'
        command(*helm, 'install', 'mic3-execution', chart)
        original = json.loads(command(*k, 'get', 'deployment/mic3-worker', '-o', 'json'))
        annotations = {bootstrap.ANNOTATION+'version': '0.1.9',
                       bootstrap.ANNOTATION+'commit': 'c'*40,
                       bootstrap.ANNOTATION+'image': original['spec']['template']['spec']['containers'][0]['image']}
        command(*k, 'annotate', 'deployment/mic3-worker', 'example.test/preserve=yes')
        current = json.loads(command(*k, 'get', 'deployment/mic3-worker', '-o', 'json'))
        if legacy_owner:
            command(*k, 'annotate', 'deployment/mic3-worker',
                    *(key+'='+value for key, value in annotations.items()))
        else:
            bootstrap.write_annotations('worker', annotations, current['metadata']['resourceVersion'])
        upgrade = [*helm, 'upgrade', 'mic3-execution', chart, '--set-string',
                   'delivery.version=0.1.10', '--set-string', 'delivery.commit='+'d'*40]
        if legacy_owner:
            result = subprocess.run([str(arg) for arg in upgrade], capture_output=True, text=True, timeout=60)
            assert result.returncode != 0 and 'kubectl-annotate' in result.stderr
            before = json.loads(command(*k, 'get', 'deployment/mic3-worker', '-o', 'json'))
            bootstrap.write_annotations('worker', annotations, before['metadata']['resourceVersion'],
                                        repair=True, existing=before['metadata']['annotations'])
            after = json.loads(command(*k, 'get', 'deployment/mic3-worker', '-o', 'json'))
            assert before['spec'] == after['spec']
            assert before['metadata']['generation'] == after['metadata']['generation']
            assert after['metadata']['annotations']['example.test/preserve'] == 'yes'
        command(*upgrade)
        command(*upgrade)  # Same-version retry also succeeds, without force-conflicts.
        final = json.loads(command(*k, 'get', 'deployment/mic3-worker', '-o', 'json'))
        assert final['metadata']['annotations'][bootstrap.ANNOTATION+'version'] == '0.1.10'
    finally:
        command(*base, 'delete', 'namespace', namespace, '--wait=false')
