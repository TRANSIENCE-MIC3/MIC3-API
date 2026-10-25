"""Execution delivery rendering and read-only gates; cluster mutations stay in Actions."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]
ANNOTATION = 'delivery.mic3.io/'
KINDS = {'Deployment': 'deployments.apps', 'ServiceAccount': 'serviceaccounts',
         'ConfigMap': 'configmaps', 'Role': 'roles.rbac.authorization.k8s.io',
         'RoleBinding': 'rolebindings.rbac.authorization.k8s.io',
         'NetworkPolicy': 'networkpolicies.networking.k8s.io'}


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def oc(*args):
    return command('oc', '--request-timeout=20s', *args)


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError('Missing/invalid live release identity; bootstrap or recover before delivery')
    return tuple(map(int, value.split('.')))


def check_deployment(doc, role, target_version, target_image, target_commit, *, deployed=False):
    annotations = doc['metadata'].get('annotations', {})
    current = annotations.get(ANNOTATION + 'version')
    if version(current) > version(target_version):
        raise ValueError('Older releases require manual recovery: ' + role)
    images = {c['name']: c['image'] for c in doc['spec']['template']['spec']['containers']}
    if current == target_version:
        if images.get(role) != target_image or annotations.get(ANNOTATION + 'commit') != target_commit:
            raise ValueError('Same version has different image/source: ' + role)
    if role != 'api' and doc['spec'].get('replicas') != 1:
        raise ValueError('Execution service is paused or scaled unexpectedly: ' + role)
    if deployed:
        status = doc.get('status', {})
        replicas = doc['spec']['replicas']
        if (current != target_version or images.get(role) != target_image
                or status.get('observedGeneration', 0) < doc['metadata']['generation']
                or any(status.get(key, 0) != replicas for key in ('updatedReplicas', 'readyReplicas', 'availableReplicas', 'replicas'))):
            raise ValueError('Incomplete rollout: ' + role)


def check_helm_status(status):
    if status['info']['status'] not in ('deployed', 'failed'):
        raise ValueError('Helm release needs operator recovery: ' + status['info']['status'])


def delivery_values(environ):
    peers = yaml.safe_load((ROOT/'deploy/okd/execution.values.yaml').read_text())['networkPolicy']['apiPeers']
    cidrs = json.loads(environ['EXECUTION_API_CIDRS'])
    if not isinstance(cidrs, list) or not cidrs:
        raise ValueError('EXECUTION_API_CIDRS must be a nonempty JSON array')
    peers += [{'ipBlock': {'cidr': str(ipaddress.ip_network(cidr, strict=True))}} for cidr in cidrs]
    return {'delivery': {'version': environ['VERSION'], 'commit': environ['COMMIT']},
        'publisher': {'image': environ['PUBLISHER_IMAGE_REF']},
        'worker': {'image': environ['WORKER_IMAGE_REF']}, 'networkPolicy': {'apiPeers': peers}}


def verification_job(environ, values):
    def secret_env(name, secret, key):
        return {'name': name, 'valueFrom': {'secretKeyRef': {'name': secret, 'key': key}}}

    env = [{'name': name, 'value': str(value)} for name, value in {
        'HOME': '/tmp', 'DB_HOST': values['database']['host'], 'DB_PORT': values['database']['port'],
        'CHECK_MODEL_IMAGE': environ['IMAGE_REF'],
        'ARTIFACT_ENDPOINT': values['modelValues']['storage']['endpoint'],
        'ARTIFACT_BUCKET': values['modelValues']['storage']['bucket']}.items()]
    env += [secret_env(name, 'mic3-postgres-credentials', key)
            for name, key in [('DB_NAME', 'database'), ('DB_USER', 'username'), ('DB_PASSWORD', 'password')]]
    env += [secret_env(name, values['artifactReaderSecret'], key) for name, key in
            [('ARTIFACT_ACCESS_KEY', 'AWS_ACCESS_KEY_ID'), ('ARTIFACT_SECRET_KEY', 'AWS_SECRET_ACCESS_KEY')]]
    return {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {
        'generateName': 'mic3-deployment-check-',
        'labels': {'app.kubernetes.io/name': 'mic3-deployment-check'},
        'annotations': {ANNOTATION+'version': environ['VERSION'], ANNOTATION+'commit': environ['COMMIT']}},
        'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 960,
            'template': {'metadata': {'labels': {'app.kubernetes.io/name': 'mic3-deployment-check'}},
                'spec': {'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                    'serviceAccountName': 'mic3-api-migration',
                    'imagePullSecrets': [{'name': name} for name in values['imagePullSecrets']],
                    'nodeSelector': values['nodeSelector'],
                    'securityContext': {'runAsNonRoot': True, 'seccompProfile': {'type': 'RuntimeDefault'}, **values['podIdentity']},
                    'containers': [{'name': 'check', 'image': environ['WORKER_IMAGE_REF'],
                        'command': ['python', '-c', (ROOT/'scripts/deployment_check.py').read_text()],
                        'env': env, 'securityContext': {'allowPrivilegeEscalation': False,
                            'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                        'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'},
                                      'limits': {'cpu': '500m', 'memory': '256Mi'}},
                        'volumeMounts': [{'name': 'temporary', 'mountPath': '/tmp'}]}],
                    'volumes': [{'name': 'temporary', 'emptyDir': {'sizeLimit': '64Mi'}}]}}}}


def merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def render(directory):
    override = delivery_values(os.environ)
    (directory/'execution-overrides.json').write_text(json.dumps(override))
    values = merge(yaml.safe_load((ROOT/'deploy/helm/execution-services/values.yaml').read_text()),
                   yaml.safe_load((ROOT/'deploy/okd/execution.values.yaml').read_text()))
    merge(values, override)
    rendered = command('helm', 'template', 'mic3-execution', str(ROOT/'deploy/helm/execution-services'),
        '--namespace', os.environ['OPENSHIFT_NAMESPACE'], '-f', str(ROOT/'deploy/okd/execution.values.yaml'),
        '-f', str(directory/'execution-overrides.json'))
    (directory/'execution.yaml').write_text(rendered)
    (directory/'verification.json').write_text(json.dumps(verification_job(os.environ, values)))


def require_permission(verb, resource):
    if oc('auth', 'can-i', verb, resource) != 'yes':
        raise ValueError('Deployer permission missing: ' + verb + ' ' + resource)


def preflight(directory):
    check_helm_status(json.loads(command('helm', 'status', 'mic3-execution', '-o', 'json')))
    for role in ('api', 'publisher', 'worker'):
        doc = json.loads(oc('get', 'deployment', 'mic3-'+role, '-o', 'json'))
        image = os.environ['IMAGE_REF' if role == 'api' else role.upper()+'_IMAGE_REF']
        check_deployment(doc, role, os.environ['VERSION'], image, os.environ['COMMIT'])
    # Upgrade only: absence is an operator bootstrap/repair task, not implicit installation.
    for doc in yaml.safe_load_all((directory/'execution.yaml').read_text()):
        resource = KINDS[doc['kind']] + '/' + doc['metadata']['name']
        oc('get', resource, '-o', 'name')
        for verb in ('get', 'patch', 'update'):
            require_permission(verb, resource)
    for verb in ('get', 'list', 'create', 'update', 'delete'):
        require_permission(verb, 'secrets')
    for verb in ('get', 'list', 'watch', 'create', 'patch'):
        require_permission(verb, 'jobs.batch')
    require_permission('get', 'pods/log')
    for resource, verbs in {'pods': ('get', 'list', 'watch'),
                            'replicasets.apps': ('get', 'list', 'watch'),
                            'events': ('get', 'list')}.items():
        for verb in verbs:
            require_permission(verb, resource)
    # No printing of Helm release contents or Secret data. Server admission is checked
    # for each existing rendered resource and for the future verification Job.
    oc('apply', '--dry-run=server', '-f', str(directory/'execution.yaml'))
    oc('create', '--dry-run=server', '-f', str(directory/'verification.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['render', 'preflight', 'verify'])
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'render':
        render(args.directory)
    elif args.operation == 'preflight':
        preflight(args.directory)
    else:
        for role in ('publisher', 'worker'):
            doc = json.loads(oc('get', 'deployment', 'mic3-'+role, '-o', 'json'))
            check_deployment(doc, role, os.environ['VERSION'], os.environ[role.upper()+'_IMAGE_REF'],
                             os.environ['COMMIT'], deployed=True)


if __name__ == '__main__':
    main()
