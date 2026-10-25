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
    parser.add_argument('--repair-ownership', action='store_true',
                        help='Release bootstrap ownership of verified annotations; stop concurrent delivery first')
    args = parser.parse_args()
    record = json.loads(args.record.read_text())
    check_helm_status(json.loads(command('helm', 'status', 'mic3-execution', '-o', 'json')))
    deployments = {role: json.loads(oc('get', 'deployment', 'mic3-'+role, '-o', 'json'))
                   for role in ('api', 'publisher', 'worker')}
    changes = planned_annotations(record, deployments)
    for role, annotations, resource_version in changes:
        if args.apply:
            write_annotations(role, annotations, resource_version,
                              repair=args.repair_ownership,
                              existing=deployments[role]['metadata'].get('annotations', {}))
        print(('Annotated ' if args.apply else 'Verified ') + role + ' against ' + record['release_tag'])


def write_annotations(role, annotations, resource_version, *, repair=False, existing=None):
    resource = 'deployment/mic3-'+role
    if not repair or any((existing or {}).get(key) != value for key, value in annotations.items()):
        oc('annotate', resource, '--overwrite', '--field-manager=mic3-bootstrap',
           '--resource-version='+resource_version, *(key+'='+value for key, value in annotations.items()))
    current = json.loads(oc('get', resource, '--show-managed-fields', '-o', 'json'))
    patch = ownership_patch(current, role, annotations)
    if len(patch) > 1:
        oc('patch', resource, '--type=json', '-p', json.dumps(patch))


def ownership_patch(doc, role, annotations):
    """Release only bootstrap-owned identity fields without changing their values.

    Update and Apply are distinct managers even when both are named helm. A
    narrowly guarded managedFields repair avoids applying an incomplete object
    as Helm (which would remove its existing spec fields), or forcing the chart.
    """
    current = doc['metadata'].get('annotations', {})
    images = {c['name']: c['image'] for c in doc['spec']['template']['spec']['containers']}
    if (any(current.get(key) != value for key, value in annotations.items())
            or images.get(role) != annotations[ANNOTATION+'image']):
        raise ValueError('Deployment changed during bootstrap; inspect before retrying')
    patch = [{'op': 'test', 'path': '/metadata/resourceVersion', 'value': doc['metadata']['resourceVersion']}]
    for index, manager in enumerate(doc['metadata'].get('managedFields', [])):
        fields = manager.get('fieldsV1', {}).get('f:metadata', {}).get('f:annotations', {})
        owned = [key for key in annotations if 'f:'+key in fields]
        if not owned or (manager['manager'] == 'helm' and manager['operation'] == 'Apply'):
            continue
        if manager['operation'] != 'Update' or manager['manager'] not in ('kubectl-annotate', 'mic3-bootstrap', 'helm'):
            raise ValueError('Unexpected delivery annotation owner; inspect before retrying')
        for key in owned:
            escaped = ('f:'+key).replace('~', '~0').replace('/', '~1')
            patch.append({'op': 'remove', 'path': f'/metadata/managedFields/{index}/fieldsV1/f:metadata/f:annotations/{escaped}'})
    return patch


if __name__ == '__main__':
    main()
