"""Opt-in checks of the exact local images that release automation will push."""
import json
import os

import docker
import pytest
import yaml

pytestmark = [pytest.mark.smoke, pytest.mark.skipif(
    os.getenv('MIC3_RUNTIME_PACKAGING_SMOKE') != '1', reason='Opt-in runtime packaging check')]


@pytest.mark.parametrize('role', ['api', 'publisher', 'worker'])
def test_runtime_dependencies_and_nonroot_filesystem(role):
    image = os.getenv(f'MIC3_{role.upper()}_IMAGE', f'mic3-{role}:packaging-check')
    modules = {'confluent_kafka': role != 'api', 'kubernetes': role == 'worker', 'boto3': role == 'worker'}
    code = ('import importlib.util, os, pathlib, shutil; '
            'assert os.getuid()!=0; '
            f'expected={modules!r}; '
            'assert all((importlib.util.find_spec(name) is not None)==present for name,present in expected.items()); '
            f'assert (shutil.which("helm") is not None)=={role == "worker"}; '
            'pathlib.Path("/tmp/check").write_text("writable"); ')
    client = docker.from_env()
    try:
        client.containers.run(image, ['python','-c',code], remove=True, read_only=True,
                              tmpfs={'/tmp':'rw,size=134217728,mode=1777'}, user='1001230000:0', network_disabled=True)
        client.containers.run(image, ['python','-m','pip','check'], remove=True, network_disabled=True)
    finally:
        client.close()


def test_worker_bundled_chart_renders_catalog_invocation():
    image = os.getenv('MIC3_WORKER_IMAGE','mic3-worker:packaging-check')
    values = {'runId':'12345678-1234-1234-1234-123456789abc','runFingerprint':'a'*64,
              'model':{'id':'packaging','mode':'check','image':'example.test/model@sha256:'+'a'*64,
                       'command':['python','-c','pass'],'workingDir':'/tmp','outputPath':'/outputs'}}
    code = ('import subprocess; '
            f'print(subprocess.check_output(["helm","template","check","/app/deploy/helm/model-run","-f","-"],input={json.dumps(values)!r},text=True))')
    client = docker.from_env()
    try:
        output = client.containers.run(image, ['python','-c',code], remove=True, read_only=True,
                                        tmpfs={'/tmp':'rw,size=134217728,mode=1777'}, user='1001230000:0', network_disabled=True)
    finally:
        client.close()
    docs = list(yaml.safe_load_all(output))
    job = next(d for d in docs if d and d['kind']=='Job')
    assert job['spec']['template']['spec']['initContainers'][0]['command']==values['model']['command']
    copy = job['spec']['template']['spec']['containers'][0]
    assert copy['name']=='copy' and 'mc cp' in copy['args'][0]
