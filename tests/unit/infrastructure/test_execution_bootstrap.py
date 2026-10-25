"""Bootstrap refuses mixed releases before changing any metadata."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'scripts'))
    spec = importlib.util.spec_from_file_location('bootstrap_identity', ROOT/'scripts/bootstrap_execution_identity.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('defect', [None, 'image', 'annotation', 'legacy'])
def test_bootstrap_checks_every_image_and_existing_annotation(monkeypatch, defect):
    bootstrap = load(monkeypatch)
    from release_record import REPOSITORIES
    record = {'format_version': 2, 'release_tag': 'v0.1.9', 'commit': 'c'*40, 'images': {
        role: {'image_repository': repo, 'image_ref': repo+':0.1.9@sha256:'+'a'*64}
        for role, repo in REPOSITORIES.items()}}
    docs = {role: {'metadata': {'resourceVersion': '1'}, 'spec': {'template': {'spec': {
        'containers': [{'name': role, 'image': image['image_ref']}]}}}}
        for role, image in record['images'].items()}
    if defect == 'image': docs['worker']['spec']['template']['spec']['containers'][0]['image'] = 'other'
    if defect == 'annotation': docs['publisher']['metadata']['annotations'] = {bootstrap.ANNOTATION+'version': '0.1.8'}
    if defect == 'legacy': record = {**record, 'format_version': 1, **record['images']['api']}
    if defect:
        with pytest.raises(ValueError): bootstrap.planned_annotations(record, docs)
    else:
        changes = bootstrap.planned_annotations(record, docs)
        assert [role for role, _, _ in changes] == ['publisher', 'worker']
        assert all(annotations[bootstrap.ANNOTATION+'commit'] == 'c'*40 for _, annotations, _ in changes)



def owned_document():
    annotations = {'delivery.mic3.io/version': '0.1.9', 'delivery.mic3.io/commit': 'c'*40,
                   'delivery.mic3.io/image': 'example.test/image@sha256:'+'a'*64}
    doc = {'metadata': {'resourceVersion': '11', 'annotations': annotations,
        'managedFields': [{'manager': 'kubectl-annotate', 'operation': 'Update',
            'fieldsV1': {'f:metadata': {'f:annotations': {**{'f:'+k: {} for k in annotations},
                                                        'f:example.test/preserve': {}}}}}]},
        'spec': {'template': {'spec': {'containers': [{'name': 'worker', 'image': annotations['delivery.mic3.io/image']}]}}}}
    return doc, annotations


def test_repair_only_releases_three_owned_keys_without_changing_values(monkeypatch):
    bootstrap = load(monkeypatch)
    doc, annotations = owned_document()
    calls = []
    def oc(*args):
        calls.append(args)
        return json.dumps(doc)
    monkeypatch.setattr(bootstrap, 'oc', oc)
    bootstrap.write_annotations('worker', annotations, '10', repair=True, existing=annotations)
    assert calls[0] == ('get', 'deployment/mic3-worker', '--show-managed-fields', '-o', 'json')
    patch = json.loads(calls[1][-1])
    assert patch[0] == {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '11'}
    assert len(patch) == 4
    assert all(op['op'] == 'remove' and op['path'].startswith('/metadata/managedFields/0/') for op in patch[1:])
    assert all('preserve' not in op['path'] for op in patch)


@pytest.mark.parametrize('defect', ['image', 'annotation', 'owner'])
def test_repair_refuses_changed_identity_or_unexpected_owner(monkeypatch, defect):
    bootstrap = load(monkeypatch)
    doc, annotations = owned_document()
    annotations = dict(annotations)
    if defect == 'image': doc['spec']['template']['spec']['containers'][0]['image'] = 'other'
    if defect == 'annotation': doc['metadata']['annotations']['delivery.mic3.io/version'] = '0.1.10'
    if defect == 'owner': doc['metadata']['managedFields'][0]['manager'] = 'another-controller'
    with pytest.raises(ValueError): bootstrap.ownership_patch(doc, 'worker', annotations)


def test_repair_preserves_helm_apply_ownership(monkeypatch):
    bootstrap = load(monkeypatch)
    doc, annotations = owned_document()
    doc['metadata']['managedFields'][0].update(manager='helm', operation='Apply')
    assert len(bootstrap.ownership_patch(doc, 'worker', annotations)) == 1
