"""Explicit local-only acceptance; requires the provisioned kind/AIStor setup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID, uuid4

from botocore.exceptions import ClientError
import pytest

from mic3_api.application.runs.execution import ReconcileExecutions
from mic3_api.core.execution_config import ExecutionSettings
from mic3_api.infrastructure.database import Database
from mic3_api.infrastructure.persistence.execution import Run
from mic3_api.infrastructure.persistence.run_execution import SqlAlchemyExecutions
from mic3_api.infrastructure.s3_artifacts import S3ArtifactInventory

pytestmark = pytest.mark.skipif(os.environ.get('MIC3_LOCAL_EXECUTION_TEST') != '1', reason='Explicit local kind/AIStor acceptance only')


def test_complete_local_execution_and_recovery():
    settings = ExecutionSettings()
    assert settings.db_host == '127.0.0.1'
    assert settings.execution_context == 'kind-mic3-local'
    assert settings.execution_namespace == 'mic3-local'
    assert settings.artifact_endpoint == 'http://127.0.0.1:19000'
    database = Database(settings.database_url)
    storage = S3ArtifactInventory.connect(settings)
    evidence = Path('deploy/local/evidence') / uuid4().hex[:12]
    evidence.mkdir(parents=True)
    processes, logs, results = [], [], {}

    def cli(*args):
        result = subprocess.run([sys.executable, '-m', *args], capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def start(module, *args, overrides=None):
        logfile = (evidence / f'{module.split(".")[-1]}-{len(processes)}.log').open('w')
        logs.append(logfile)
        process = subprocess.Popen([sys.executable, '-m', module, *args], stdout=logfile, stderr=subprocess.STDOUT,
                                   env={**os.environ, **(overrides or {})}, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        processes.append(process)
        return process

    def wait(run_id, predicate, timeout=300):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with database.open_session() as session:
                row = session.get(Run, UUID(run_id))
                if predicate(row):
                    return row
                assert row.status != 'failed', f'{run_id}: {row.failure_code}; logs: {evidence}'
            time.sleep(0.5)
        pytest.fail(f'Timed out waiting for {run_id}; logs: {evidence}')

    def submit(release_id, mode):
        params = evidence / 'parameters.json'
        params.write_text(json.dumps({'mode':mode}))
        return json.loads(cli('mic3_api.cli','submit-run','--release-id',release_id,'--parameters',str(params)))['id']

    def inspect_run(run_id):
        return json.loads(cli('mic3_api.cli','show-run','--run-id',run_id))

    local = ['kubectl','--kubeconfig',str(settings.execution_kubeconfig),'--context',settings.execution_context,'--namespace',settings.execution_namespace]
    try:
        config = json.loads(Path('integrations/eu_mfa/release.json').read_text())
        image = config['image_digest']
        script = "import pathlib,time; time.sleep(25); pathlib.Path('/outputs/result.txt').write_text('local verification')"
        def mode(code, required=None):
            definition = {'arguments':['python','-c',code],'working_directory':'/tmp','output_directory':'/outputs'}
            if required is not None:
                definition['required_outputs'] = required
            return definition
        config = {'model_id':'local-check-'+uuid4().hex[:8], 'display_name':'Local execution verification', 'image_digest':image,
                  'execution_definition':{'modes':{
                      'slow':mode(script), 'failure':mode('raise SystemExit(7)'),
                      'empty':mode('pass'), 'missing':mode("import pathlib; pathlib.Path('/outputs/partial.txt').write_text('partial')",['required.txt'])}}}
        config_path = evidence / 'release.json'
        config_path.write_text(json.dumps(config))
        release_id = json.loads(cli('mic3_api.cli','register-model-release','--config',str(config_path)))['id']
        # Persist intent but simulate a crash before the Kubernetes call.
        intent_id = submit(release_id,'slow')
        cli('mic3_api.publisher','--once')
        # A historical event may precede this test; drain through acceptance only.
        for _ in range(20):
            cli('mic3_api.worker','--once')
            with database.open_session() as s:
                if s.get(Run,UUID(intent_id)).accepted_at:
                    break
        store = SqlAlchemyExecutions(database.engine)
        assert ReconcileExecutions(store, None, None, settings.execution_namespace, 1).claim_next() == UUID(intent_id)
        start('mic3_api.publisher')
        worker = start('mic3_api.worker','--execute')
        running = wait(intent_id, lambda row: row.job_uid is not None)
        job_uid = running.job_uid
        # Simulate abrupt worker loss while the model's deliberate sleep is active.
        worker.kill(); worker.wait(timeout=20)
        worker = start('mic3_api.worker','--execute')
        completed = wait(intent_id, lambda row: row.status == 'succeeded')
        assert completed.job_uid == job_uid
        results['recovery'] = inspect_run(intent_id)
        assert len(results['recovery']['artifacts']) == 1

        for mode_name, code in [('failure','model_failed'),('empty','outputs_empty'),('missing','required_outputs_missing')]:
            run_id = submit(release_id,mode_name)
            row = wait(run_id,lambda row: row.status == 'failed')
            assert row.failure_code == code
            results[mode_name] = inspect_run(run_id)
        assert len(results['missing']['artifacts']) == 1

        # A missing bucket makes the existing copy container fail, without changing credentials.
        worker.kill(); worker.wait(timeout=20)
        bad_values = evidence / 'copy-failure.values.yaml'
        bad_values.write_text(Path('deploy/local/model-run.values.yaml').read_text()+'\nstorage:\n  bucket: mic3-missing-bucket\n')
        worker = start('mic3_api.worker','--execute',overrides={'EXECUTION_VALUES_FILE':str(bad_values)})
        copy_id = submit(release_id,'slow')
        assert wait(copy_id,lambda row: row.status=='failed').failure_code == 'upload_failed'
        results['upload_failure'] = inspect_run(copy_id)
        worker.kill(); worker.wait(timeout=20)
        worker = start('mic3_api.worker','--execute')

        eumfa = json.loads(cli('mic3_api.cli','register-model-release','--config','integrations/eu_mfa/release.json'))
        eu_id = submit(eumfa['id'],'buildings')
        wait(eu_id,lambda row: row.status == 'succeeded')
        results['eu_mfa'] = inspect_run(eu_id)
        artifacts = results['eu_mfa']['artifacts']
        assert len(artifacts) == 12 and all(a['size_bytes'] > 0 for a in artifacts)
        # Retained Job identity is the same one recorded in PostgreSQL.
        job = json.loads(subprocess.check_output([*local,'get','job','mic3-run-'+eu_id,'-o','json'],text=True))
        assert job['metadata']['uid'] == results['eu_mfa']['job_uid']

        subprocess.run([*local,'rollout','restart','deployment/mic3-aistor'],check=True,capture_output=True)
        subprocess.run([*local,'rollout','status','deployment/mic3-aistor','--timeout=120s'],check=True,capture_output=True)
        for artifact in artifacts:
            head = storage.client.head_object(Bucket=artifact['bucket'],Key=artifact['object_key'])
            assert head['ContentLength'] == artifact['size_bytes']
        # Read one object's bytes, not just bucket metadata.
        response=storage.client.get_object(Bucket=artifacts[0]['bucket'],Key=artifacts[0]['object_key'])
        assert len(response['Body'].read())==artifacts[0]['size_bytes']
        response['Body'].close()
        for operation in ('put_object','delete_object'):
            args={'Bucket':settings.artifact_bucket,'Key':'permission-check-'+uuid4().hex}
            if operation=='put_object': args['Body']=b'test'
            with pytest.raises(ClientError) as error:
                getattr(storage.client,operation)(**args)
            assert error.value.response['Error']['Code']=='AccessDenied'
        cli('mic3_api.publisher','--once')
        for result in results.values():
            inspected=inspect_run(result['id'])
            terminal=[e for e in inspected['events'] if e['event_type'] in ('run.succeeded','run.failed')]
            assert len(terminal)==1 and terminal[0]['published_at']
        (evidence/'results.json').write_text(json.dumps(results,indent=2))
        print('Local execution evidence:',evidence)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=20)
        for logfile in logs:
            logfile.close()
        storage.close()
        database.dispose()
