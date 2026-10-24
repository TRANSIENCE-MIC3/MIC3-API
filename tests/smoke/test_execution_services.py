"""Opt-in packaged execution in an explicitly selected, Docker-backed kind cluster.

Creates/deletes only a random test namespace. Existing AIStor is read/reused;
objects use fresh run UUIDs and are retained. No production kubeconfig fallback.
Prerequisites: local worker/publisher images, kind, kubectl, Helm, provisioned
mic3-local AIStor and its reader/uploader Secrets, cached model/copy images.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlparse
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
import pytest
import yaml

from tests.smoke.test_kafka_packaging import make_ca, identity

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.smoke, pytest.mark.skipif(
    os.getenv('MIC3_EXECUTION_SERVICES_SMOKE') != '1', reason='Opt-in isolated kind smoke')]


def command(args, data=None, timeout=180):
    result = subprocess.run([str(a) for a in args], input=data, text=True,
                            capture_output=True, timeout=timeout)
    # Never echo stdin: it may contain temporary credentials.
    assert result.returncode == 0, f'{args[:4]} failed: {result.stderr}'
    return result.stdout


def test_packaged_execution_and_recovery(tmp_path):
    kubeconfig = os.environ['MIC3_KIND_KUBECONFIG']
    context = os.environ['MIC3_KIND_CONTEXT']
    assert context.startswith('kind-'), 'Only explicitly selected kind contexts are allowed'
    base = ['kubectl', '--kubeconfig', kubeconfig, '--context', context]
    config = json.loads(command([*base, 'config', 'view', '--minify', '-o', 'json']))
    assert urlparse(config['clusters'][0]['cluster']['server']).hostname in {'127.0.0.1','localhost','::1'}
    nodes = json.loads(command([*base,'get','nodes','-o','json']))['items']
    assert nodes and all(n['spec'].get('providerID','').startswith('kind://') for n in nodes)
    assert len(nodes)==1, 'This smoke imports cached images into one local kind node'
    cluster = context.removeprefix('kind-')
    kind = os.getenv('MIC3_KIND_BINARY', str(ROOT/'deploy/local/bin/kind.exe'))
    assert cluster in command([kind,'get','clusters']).splitlines()
    namespace = 'mic3-packaging-' + uuid4().hex[:10]
    k = [*base,'-n',namespace]
    evidence = ROOT/'deploy/local/evidence'/namespace
    evidence.mkdir(parents=True)

    def apply(docs):
        command([*k,'apply','-f','-'], yaml.safe_dump_all(docs))

    def secret(name, data):
        apply([{'apiVersion':'v1','kind':'Secret','metadata':{'name':name},
                'data':{key:base64.b64encode(value if isinstance(value,bytes) else value.encode()).decode()
                        for key,value in data.items()}}])

    def render(chart, values):
        path = tmp_path/(chart+'.yaml'); path.write_text(yaml.safe_dump(values))
        return list(yaml.safe_load_all(command(['helm','template','check',ROOT/'deploy/helm'/chart,
                                               '-n',namespace,'-f',path])))

    def wait_for(fn, timeout=180):
        until = time.monotonic()+timeout
        while time.monotonic()<until:
            value = fn()
            if value: return value
            time.sleep(1)
        pytest.fail(f'Timeout; diagnostics: {evidence}')

    def exec_python(code):
        return command([*k,'exec','operator','--','python','-c',code])

    def cli(*args):
        return json.loads(command([*k,'exec','operator','--','python','-m','mic3_api.cli',*args]))

    def load(image):
        def normalize(reference):
            if '/' not in reference:
                return 'docker.io/library/'+reference
            first = reference.split('/')[0]
            return reference if '.' in first or ':' in first or first=='localhost' else 'docker.io/'+reference
        refs = json.loads(command(['docker','image','inspect',image]))[0]['RepoDigests']
        assert refs, f'{image} has no immutable reference'
        ref = refs[0]
        # kind imports tags; ensure the digest alias resolves without a registry.
        node = nodes[0]['metadata']['name']
        names = command(['docker','exec',node,'ctr','-n','k8s.io','images','ls','-q']).splitlines()
        normalized = normalize(image)
        target = normalize(ref)
        if target not in names:
            export_image = image
            if '@' in image:
                export_image = 'mic3-fixture:' + uuid4().hex
                command(['docker','tag',image,export_image])
                normalized = 'docker.io/library/'+export_image
            # Import only the cached host platform, not missing multiarch siblings.
            with subprocess.Popen(['docker','image','save','--platform','linux/amd64',export_image],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as export:
                imported = subprocess.run(['docker','exec','-i',node,'ctr','-n','k8s.io','images',
                    'import','--platform','linux/amd64','--digests','-'], stdin=export.stdout,
                    capture_output=True,timeout=300)
                export.stdout.close()
                assert export.wait(timeout=30)==0
                assert imported.returncode==0, imported.stderr.decode()
            names = command(['docker','exec',node,'ctr','-n','k8s.io','images','ls','-q']).splitlines()
            if target not in names:
                listing = command(['docker','exec',node,'ctr','-n','k8s.io','images','ls'])
                row = next(line.split() for line in listing.splitlines() if line.split()[0]==normalized)
                target = target.split('@')[0]+'@'+row[2]
                command(['docker','exec',node,'ctr','-n','k8s.io','images','tag',normalized,target])
            if export_image != image:
                command(['docker','image','rm',export_image])
        return target

    publisher = load(os.getenv('MIC3_PUBLISHER_IMAGE','mic3-publisher:packaging-check'))
    worker = load(os.getenv('MIC3_WORKER_IMAGE','mic3-worker:packaging-check'))
    postgres = load('postgres@sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280')
    load('apache/kafka:4.1.1')
    command([*base,'create','namespace',namespace])
    try:
        for name in ['mic3-artifact-reader-credentials','mic3-artifact-uploader-credentials','ghcr-pull']:
            source = json.loads(command([*base,'-n','mic3-local','get','secret',name,'-o','json']))
            apply([{'apiVersion':'v1','kind':'Secret','metadata':{'name':name},
                    'type':source.get('type','Opaque'),'data':source['data']}])
        ca_key, ca_cert = make_ca()
        for role in ['broker','publisher','worker','operator']:
            dns = [f'mic3-kafka.{namespace}.svc.cluster.local',
                   f'mic3-kafka-0.mic3-kafka-headless.{namespace}.svc.cluster.local'] if role=='broker' else []
            secret('mic3-kafka-'+role+'-tls',identity(role,ca_key,ca_cert,dns))
        apply([{'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'mic3-kafka-trust'},
                'data':{'ca.crt':ca_cert.public_bytes(serialization.Encoding.PEM).decode()}}])
        apply(render('kafka',{'certificates':{'enabled':False},'broker':{'enabled':True,
            'clusterId':'MkMza2Fma2Fsb2NhbDAwMQ'},'podIdentity':{'runAsUser':10001,'runAsGroup':10001,'fsGroup':10001}}))
        command([*k,'rollout','status','statefulset/mic3-kafka','--timeout=180s'],timeout=200)
        command([*k,'wait','--for=condition=complete','job/mic3-kafka-bootstrap','--timeout=180s'],timeout=200)
        password = uuid4().hex
        secret('fixture-postgres',{'password':password})
        apply([{'apiVersion':'v1','kind':'Pod','metadata':{'name':'postgres','labels':{'app.kubernetes.io/name':'mic3-postgres'}},
                'spec':{'automountServiceAccountToken':False,'containers':[{'name':'postgres','image':postgres,
                    'env':[{'name':'POSTGRES_PASSWORD','valueFrom':{'secretKeyRef':{'name':'fixture-postgres','key':'password'}}},
                           {'name':'POSTGRES_DB','value':'mic3'}]}]}},
               {'apiVersion':'v1','kind':'Service','metadata':{'name':'mic3-postgres'},
                'spec':{'selector':{'app.kubernetes.io/name':'mic3-postgres'},'ports':[{'port':5432}]}}])
        command([*k,'wait','--for=condition=Ready','pod/postgres','--timeout=120s'])
        apply([{'apiVersion':'v1','kind':'Pod','metadata':{'name':'operator'},'spec':{
            'automountServiceAccountToken':False,'containers':[{'name':'operator','image':worker,
            'command':['python','-c','import time; time.sleep(3600)'],'env':[
                {'name':'DB_HOST','value':'mic3-postgres'},{'name':'DB_NAME','value':'mic3'},
                {'name':'DB_USER','value':'postgres'},
                {'name':'DB_PASSWORD','valueFrom':{'secretKeyRef':{'name':'fixture-postgres','key':'password'}}}]}]}}])
        command([*k,'wait','--for=condition=Ready','pod/operator','--timeout=120s'])
        command([*k,'exec','operator','--','python','-m','alembic','upgrade','head'])
        # Temporary roles are created only inside this isolated fixture database.
        for role in ['publisher','worker']:
            secret('mic3-'+role+'-postgres-credentials',{'database':'mic3','username':role,'password':password})
        sql = f"CREATE ROLE publisher LOGIN PASSWORD '{password}'; CREATE ROLE worker LOGIN PASSWORD '{password}'; "
        sql += ('REVOKE CREATE ON SCHEMA public FROM PUBLIC; GRANT USAGE ON SCHEMA public TO publisher, worker; '
                'GRANT SELECT, UPDATE ON outbox_events TO publisher; '
                'GRANT SELECT ON models, model_releases, runs TO worker; '
                'GRANT UPDATE(id) ON model_releases TO worker; GRANT UPDATE ON runs TO worker; '
                'GRANT INSERT ON run_artifacts, outbox_events, rejected_messages TO worker; '
                'GRANT SELECT(created_at) ON outbox_events TO worker; '
                'GRANT SELECT(topic, partition, "offset") ON rejected_messages TO worker;')
        command([*k,'exec','-i','postgres','--','psql','-U','postgres','-d','mic3','-v','ON_ERROR_STOP=1'],sql)
        api_ip = json.loads(command([*base,'get','service','kubernetes','-n','default','-o','json']))['spec']['clusterIP']
        values = {'publisher':{'image':publisher},'worker':{'image':worker},'imagePullSecrets':[],
            'podIdentity':{'runAsUser':1001230000,'runAsGroup':1001230000,'fsGroup':1001230000},
            'modelValues':{'podIdentity':{'runAsUser':1000,'runAsGroup':1000,'fsGroup':1000},
                'storage':{'endpoint':'http://mic3-aistor.mic3-local.svc.cluster.local:9000'}},
            'networkPolicy':{'apiPeers':[{'ipBlock':{'cidr':api_ip+'/32'}}],
                'storagePeers':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'mic3-local'}},
                                 'podSelector':{'matchLabels':{'app.kubernetes.io/name':'mic3-aistor'}}}]}}
        apply(render('execution-services',values))
        # Real API authorization, using admin impersonation of each exact account.
        for role,verb,resource,expected in [('publisher','create','jobs','no'),('publisher','get','jobs','no'),
            ('worker','create','jobs','yes'),('worker','get','jobs','yes'),('worker','list','pods','yes'),
            ('worker','delete','jobs','no'),('worker','list','jobs','no'),('worker','get','secrets','no'),
            ('worker','get','pods','no'),('worker','get','nodes','no')]:
            result = subprocess.run([*k,'auth','can-i',verb,resource,
                '--as=system:serviceaccount:'+namespace+':mic3-'+role],capture_output=True,text=True)
            assert result.stdout.strip()==expected
        config = {'model_id':'packaging-'+uuid4().hex[:8],'display_name':'Packaged execution check',
            'image_digest':json.loads((ROOT/'integrations/eu_mfa/release.json').read_text())['image_digest'],
            'execution_definition':{'modes':{'slow':{'arguments':['python','-c',
                "import pathlib,time; time.sleep(75); pathlib.Path('/outputs/result.txt').write_text('packaged verification')"],
                'working_directory':'/tmp','output_directory':'/outputs','required_outputs':['result.txt']}}}}
        exec_python('from pathlib import Path; Path("/tmp/release.json").write_text('+repr(json.dumps(config))+'); Path("/tmp/params.json").write_text(\'{"mode":"slow"}\')')
        release_id = cli('register-model-release','--config','/tmp/release.json')['id']
        run_id = cli('submit-run','--release-id',release_id,'--parameters','/tmp/params.json')['id']
        values['publisher']['replicas'] = values['worker']['replicas'] = 1
        apply(render('execution-services',values))
        command([*k,'rollout','status','deployment/mic3-worker','--timeout=120s'])
        command([*k,'rollout','status','deployment/mic3-publisher','--timeout=120s'])
        rejection_probe = '''
from mic3_api.core.config import DatabaseSettings
from mic3_api.infrastructure.database import Database
from mic3_api.infrastructure.persistence.rejections import SqlAlchemyRejections
from mic3_api.application.runs.consumption import RecordLocation, Rejection
db = Database(DatabaseSettings().database_url)
with db.open_session() as s:
    store = SqlAlchemyRejections(s)
    for _ in range(2):
        store.record(RecordLocation('fixture.invalid',0,0), Rejection('invalid_request','fixture'))
db.dispose()
'''
        command([*k,'exec','deployment/mic3-worker','--','python','-c',rejection_probe])
        denial_probe = '''
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from mic3_api.core.config import DatabaseSettings
engine = create_engine(DatabaseSettings().database_url)
for sql in ['SELECT * FROM users', 'CREATE TABLE forbidden(id int)', 'DELETE FROM runs']:
    with engine.connect() as c:
        try: c.execute(text(sql))
        except DBAPIError as e: assert e.orig.sqlstate == '42501'
        else: raise AssertionError('Unexpected SQL permission: '+sql)
        finally: c.rollback()
engine.dispose()
'''
        for role in ['publisher','worker']:
            command([*k,'exec','deployment/mic3-'+role,'--','python','-c',denial_probe])
        def inspect(): return cli('show-run','--run-id',run_id)
        running = wait_for(lambda: (r if (r:=inspect()).get('job_uid') else None))
        job_uid = running['job_uid']
        active_job = json.loads(command([*k,'get','job','mic3-run-'+run_id,'-o','json']))
        assert not active_job.get('status',{}).get('completionTime'), 'Restart must occur during execution'
        old_pods = json.loads(command([*k,'get','pods','-l','app.kubernetes.io/name=mic3-worker','-o','json']))
        old_uid = old_pods['items'][0]['metadata']['uid']
        command([*k,'delete','pod','-l','app.kubernetes.io/name=mic3-worker','--grace-period=0','--force'])
        def restarted():
            pods = json.loads(command([*k,'get','pods','-l','app.kubernetes.io/name=mic3-worker','-o','json']))
            return any(p['metadata']['uid']!=old_uid and p.get('status',{}).get('phase')=='Running' for p in pods['items'])
        wait_for(restarted,120)
        def terminal():
            r = inspect()
            assert r['status'] != 'failed', r
            return r if r['status']=='succeeded' and any(e['event_type']=='run.succeeded' and e['published_at'] for e in r['events']) else None
        result = wait_for(terminal,240)
        assert result['job_uid']==job_uid and len(result['artifacts'])==1
        assert result['artifacts'][0]['size_bytes'] == len('packaged verification')
        job = json.loads(command([*k,'get','job','mic3-run-'+run_id,'-o','json']))
        assert job['metadata']['uid']==job_uid
        artifact = result['artifacts'][0]
        storage_probe = '''
from botocore.exceptions import ClientError
from mic3_api.core.execution_config import ExecutionSettings
from mic3_api.infrastructure.s3_artifacts import S3ArtifactInventory
storage = S3ArtifactInventory.connect(ExecutionSettings())
artifact = ARTIFACT
response = storage.client.get_object(Bucket=artifact['bucket'],Key=artifact['object_key'])
assert response['Body'].read() == b'packaged verification'
response['Body'].close()
for operation in ['put_object','delete_object']:
    args = {'Bucket':artifact['bucket'],'Key':artifact['object_key']+'.permission-check'}
    if operation=='put_object': args['Body']=b'forbidden'
    try: getattr(storage.client,operation)(**args)
    except ClientError as e: assert e.response['Error']['Code']=='AccessDenied'
    else: raise AssertionError('Reader unexpectedly allowed '+operation)
storage.close()
'''.replace('ARTIFACT',repr(artifact))
        command([*k,'exec','deployment/mic3-worker','--','python','-c',storage_probe])
        (evidence/'results.json').write_text(json.dumps(result,indent=2))
        print('Packaged kind evidence:',evidence)
    finally:
        for target in ['deployment/mic3-publisher','deployment/mic3-worker','statefulset/mic3-kafka']:
            logs = subprocess.run([*k,'logs',target,'--tail=200'],capture_output=True,text=True)
            (evidence/(target.split('/')[-1]+'.log')).write_text(logs.stdout+logs.stderr)
        events = subprocess.run([*k,'get','events','-o','json'],capture_output=True,text=True)
        (evidence/'events.json').write_text(events.stdout)
        command([*base,'delete','namespace',namespace,'--wait=false'])
