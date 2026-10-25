"""One-time operator annotation of an existing execution installation.

Run with the existing trusted release.json. Validate every live image before
writing anything; never use this to relabel a mismatched/partially updated release.
"""
import argparse
import json
from pathlib import Path

from execution_delivery import ANNOTATION, check_helm_status, command, oc
from release_record import validate


def planned_annotations(record, deployments):
    images = validate(record, record['release_tag'], record['commit'])
    if set(images) != {'api', 'publisher', 'worker'}:
        raise ValueError('Bootstrap requires a complete three-image release record')
    changes = []
    for role in ('api', 'publisher', 'worker'):
        doc = deployments[role]
        live = {c['name']: c['image'] for c in doc['spec']['template']['spec']['containers']}
        if live.get(role) != images[role]['image_ref']:
            raise ValueError('Live image differs from release record: '+role)
        expected = {ANNOTATION+'version': record['release_tag'][1:],
                    ANNOTATION+'commit': record['commit'], ANNOTATION+'image': images[role]['image_ref']}
        existing = doc['metadata'].get('annotations', {})
        if any(key in existing and existing[key] != value for key, value in expected.items()):
            raise ValueError('Existing release annotation differs: '+role)
        if role != 'api':
            changes.append((role, expected, doc['metadata']['resourceVersion']))
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Apply verified metadata; otherwise inspect only')
    args = parser.parse_args()
    record = json.loads(args.record.read_text())
    check_helm_status(json.loads(command('helm', 'status', 'mic3-execution', '-o', 'json')))
    deployments = {role: json.loads(oc('get', 'deployment', 'mic3-'+role, '-o', 'json'))
                   for role in ('api', 'publisher', 'worker')}
    changes = planned_annotations(record, deployments)
    for role, annotations, resource_version in changes:
        if args.apply:
            oc('annotate', 'deployment/mic3-'+role, '--overwrite', '--resource-version='+resource_version,
               *(key+'='+value for key, value in annotations.items()))
        print(('Annotated ' if args.apply else 'Verified ') + role + ' against ' + record['release_tag'])


if __name__ == '__main__':
    main()
