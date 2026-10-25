"""Bootstrap refuses mixed releases before changing any metadata."""
import importlib.util
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
