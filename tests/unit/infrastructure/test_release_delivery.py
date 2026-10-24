"""Release contracts and failure paths using simulated GitHub/registry responses."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('release_record', ROOT/'scripts/release_record.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
TAG, COMMIT = 'v0.1.9', 'c'*40


def record():
    return {'format_version':2,'release_tag':TAG,'commit':COMMIT,'images':{
        role:{'image_repository':repo,'image_ref':repo+':0.1.9@sha256:'+'a'*64}
        for role,repo in release.REPOSITORIES.items()}}


class Registry:
    def __init__(self):
        self.existing = set()
        self.pushed = []
        self.calls = []
        self.push_failure = None
        self.remote_failure = False
        self.bad_local = False

    def __call__(self, *args):
        self.calls.append(args)
        ref = args[-1]
        if args[:3] == ('docker','manifest','inspect'):
            if '@' in ref:
                if self.remote_failure:
                    raise subprocess.CalledProcessError(1,args,stderr='unavailable')
                return '{}'
            if ref in self.existing: return '{}'
            raise subprocess.CalledProcessError(1,args,stderr='manifest unknown')
        if args[:3] == ('docker','image','inspect'):
            repo = ref.rsplit(':',1)[0]
            return json.dumps([{'Os':'linux','Architecture':'amd64','Config':{'Labels':{
                'org.opencontainers.image.revision':'bad' if self.bad_local else COMMIT,
                'org.opencontainers.image.version':'0.1.9'}},
                'RepoDigests':[repo+'@sha256:'+'a'*64]}])
        if args[:2] == ('docker','push'):
            if self.push_failure and self.push_failure in ref:
                raise subprocess.CalledProcessError(1,args,stderr='push failed')
            self.pushed.append(ref); self.existing.add(ref)
            return ''
        raise AssertionError(args)


def test_publish_complete_record_only_after_verifying_every_remote(monkeypatch):
    registry = Registry(); monkeypatch.setattr(release,'run',registry)
    assert release.publish(TAG,COMMIT) == record()
    assert len(registry.pushed)==3
    first_push = next(i for i,args in enumerate(registry.calls) if args[:2]==('docker','push'))
    assert sum(args[:3]==('docker','image','inspect') for args in registry.calls[:first_push])==3
    assert all('@sha256:' in args[-1] for args in registry.calls[-3:])


@pytest.mark.parametrize('role', ['api','publisher','worker'])
def test_any_existing_version_blocks_all_pushes(monkeypatch, role):
    registry = Registry(); registry.existing.add(release.REPOSITORIES[role]+':0.1.9')
    monkeypatch.setattr(release,'run',registry)
    with pytest.raises(ValueError,match='Version image exists'): release.publish(TAG,COMMIT)
    assert registry.pushed == []


def test_partial_publication_cannot_be_overwritten_on_retry(monkeypatch):
    registry = Registry(); registry.push_failure='publisher'
    monkeypatch.setattr(release,'run',registry)
    with pytest.raises(subprocess.CalledProcessError): release.publish(TAG,COMMIT)
    assert len(registry.pushed)==1
    registry.push_failure=None
    with pytest.raises(ValueError): release.publish(TAG,COMMIT)
    assert len(registry.pushed)==1


def test_registry_auth_error_is_not_absence(monkeypatch):
    def failed(*args): raise subprocess.CalledProcessError(1,args,stderr='unauthorized')
    monkeypatch.setattr(release,'run',failed)
    with pytest.raises(RuntimeError): release.preflight('0.1.9')


def test_wrong_local_source_prevents_any_publication(monkeypatch):
    registry = Registry(); registry.bad_local=True; monkeypatch.setattr(release,'run',registry)
    with pytest.raises(ValueError,match='local'): release.publish(TAG,COMMIT)
    assert not registry.pushed


@pytest.mark.parametrize('change', ['tag','commit','repository','digest','missing','extra','format'])
def test_invalid_records_are_rejected(change):
    data=record()
    if change=='tag': data['release_tag']='v0.1.8'
    elif change=='commit': data['commit']='d'*40
    elif change=='repository': data['images']['worker']['image_repository']='untrusted/worker'
    elif change=='digest': data['images']['api']['image_ref']=release.REPOSITORIES['api']+':latest'
    elif change=='missing': del data['images']['publisher']
    elif change=='extra': data['images']['other']=copy.deepcopy(data['images']['api'])
    elif change=='format': data['format_version']=True
    with pytest.raises(ValueError): release.validate(data,TAG,COMMIT)


def test_legacy_record_returns_only_api():
    data=record(); legacy={k:data[k] for k in ['release_tag','commit']}
    legacy.update(format_version=1,**data['images']['api'])
    assert release.validate(legacy,TAG,COMMIT)=={'api':data['images']['api']}


@pytest.mark.parametrize('legacy', [False, True])
def test_reuse_validates_remote_references_and_exports_only_api(monkeypatch,tmp_path,legacy):
    data=record()
    if legacy:
        data={'format_version':1,'release_tag':TAG,'commit':COMMIT,**data['images']['api']}
    path=tmp_path/'release.json'; path.write_text(json.dumps(data))
    output=tmp_path/'output'; summary=tmp_path/'summary'
    registry=Registry(); monkeypatch.setattr(release,'run',registry)
    monkeypatch.setenv('RELEASE_TAG',TAG); monkeypatch.setenv('COMMIT',COMMIT)
    monkeypatch.setenv('GITHUB_OUTPUT',str(output)); monkeypatch.setenv('GITHUB_STEP_SUMMARY',str(summary))
    monkeypatch.setattr('sys.argv',['release_record.py','validate','--record',str(path)])
    release.main()
    assert output.read_text()=='image_ref='+record()['images']['api']['image_ref']+'\n'
    assert len(registry.calls)==(1 if legacy else 3)
    assert all(args[:3]==('docker','manifest','inspect') for args in registry.calls)
    assert not registry.pushed
    if not legacy:
        assert all(image['image_ref'] in summary.read_text() for image in record()['images'].values())


@pytest.mark.parametrize('status,required', [(404,True),(403,None),(500,None),(200,False)])
def test_github_discovery_requires_confirmed_absence(monkeypatch,tmp_path,status,required):
    path=tmp_path/'release.json'
    calls=[]
    def gh(*args):
        calls.append(args)
        if args[:2]==('gh','api'):
            if status!=200: raise subprocess.CalledProcessError(1,args,stderr=f'(HTTP {status})')
            return json.dumps({'draft':False,'prerelease':False,'assets':[{'name':'release.json'}]})
        assert args[:3]==('gh','release','download'); path.write_text(json.dumps(record())); return ''
    monkeypatch.setattr(release,'run',gh)
    if required is None:
        with pytest.raises(RuntimeError): release.discover(TAG,'owner/repo',path)
    else:
        assert release.discover(TAG,'owner/repo',path) is required
    assert len(calls)==(2 if status==200 else 1)


@pytest.mark.parametrize('metadata', [{'draft':True,'prerelease':False,'assets':[{'name':'release.json'}]},
    {'draft':False,'prerelease':True,'assets':[{'name':'release.json'}]},
    {'draft':False,'prerelease':False,'assets':[]}])
def test_incomplete_release_cannot_trigger_republication(monkeypatch,tmp_path,metadata):
    monkeypatch.setattr(release,'run',lambda *args:json.dumps(metadata))
    with pytest.raises(ValueError): release.discover(TAG,'owner/repo',tmp_path/'release.json')


@pytest.mark.parametrize('operation', ['publish','validate'])
def test_remote_failure_produces_no_record_or_deployment_output(monkeypatch,tmp_path,operation):
    registry=Registry(); registry.remote_failure=True; monkeypatch.setattr(release,'run',registry)
    path=tmp_path/'release.json'; output=tmp_path/'output'
    if operation=='validate': path.write_text(json.dumps(record()))
    monkeypatch.setenv('RELEASE_TAG',TAG); monkeypatch.setenv('COMMIT',COMMIT)
    monkeypatch.setenv('GITHUB_OUTPUT',str(output)); monkeypatch.setenv('GITHUB_STEP_SUMMARY',str(tmp_path/'summary'))
    monkeypatch.setattr('sys.argv',['release_record.py',operation,'--record',str(path)])
    with pytest.raises(subprocess.CalledProcessError): release.main()
    assert not output.exists()
    if operation=='publish': assert not path.exists()


def test_workflow_gates_publication_and_only_deploys_api():
    workflow=yaml.load((ROOT/'.github/workflows/publish-image.yml').read_text(),Loader=yaml.BaseLoader)
    prepare=workflow['jobs']['prepare']; deploy=workflow['jobs']['deploy']
    assert prepare['timeout-minutes']=='60'
    steps=prepare['steps']; names=[s.get('name','') for s in steps]
    build=names.index('Build all three local images')
    smoke=names.index('Validate packaged runtimes and secured Kafka')
    publish=names.index('Publish tested images and verify immutable references')
    save=names.index('Save immutable release record')
    assert build<smoke<publish<save<len(steps)-1
    assert 'api publisher worker' in steps[build]['run'] and '--load' in steps[build]['run']
    assert '--push' not in steps[build]['run']
    for i in [build,smoke,publish,save]: assert steps[i]['if']=="steps.record.outputs.build_required == 'true'"
    assert 'MIC3_EXECUTION_SERVICES_SMOKE' not in steps[smoke]['env']
    assert deploy['needs']=='prepare' and deploy.get('if') is None
    assert deploy['env']['IMAGE_REF']=='${{ needs.prepare.outputs.image_ref }}'
    assert not any('publisher' in s.get('run','') or 'worker' in s.get('run','') for s in deploy['steps'])
    assert prepare['concurrency']['cancel-in-progress']=='false'
    assert deploy['concurrency']['group']=='eosc-development'
    helm=names.index('Install verified Helm for chart tests'); tests=names.index('Run unit and integration tests')
    assert helm<tests<build and 'sha256sum --check' in steps[helm]['run']
    assert 'command -v helm' in steps[tests]['run']
    assert any(s.get('with',{}).get('path')=='.workflow-tools' and s['with']['ref']=='${{ github.workflow_sha }}' for s in steps)
