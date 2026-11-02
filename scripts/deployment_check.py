"""In-cluster delivery check; embedded in a Job, never run on the Actions host.

Uses ordinary catalog/submission boundaries. No Kubernetes or Kafka credentials,
no schema creation, no retries of submission, and no deletion of diagnostic data.
"""
import json
import os
import sys
import time

EXPECTED = b'MIC3 deployment verification\n'
MODEL_ID = 'mic3-deployment-check'


def register_and_submit(session, image):
    from mic3_api.application.models.catalog import RegisterRelease
    from mic3_api.application.runs.submission import SubmitRun
    from mic3_api.infrastructure.persistence.model_catalog import SqlAlchemyModelCatalog
    from mic3_api.infrastructure.persistence.run_submissions import SqlAlchemyRunSubmissions

    catalog = SqlAlchemyModelCatalog(session)
    definition = {'model_id': MODEL_ID, 'display_name': 'MIC3 deployment check (diagnostic)',
        'image_digest': image, 'execution_definition': {'modes': {'check': {
            'arguments': ['python', '-c',
                "from pathlib import Path; Path('/outputs/result.txt').write_bytes(" + repr(EXPECTED) + ")"],
            'working_directory': '/tmp', 'output_directory': '/outputs',
            'required_outputs': ['result.txt']}}}}
    release = RegisterRelease(catalog).execute(definition)
    run = SubmitRun(catalog, SqlAlchemyRunSubmissions(session)).execute(release.id, {'mode': 'check'})
    print(json.dumps({'run_id': str(run.id)}), flush=True)
    return run.id


def verify_result(result, storage, bucket):
    """False means publication/processing is pending; terminal defects fail closed."""
    if result['status'] == 'failed':
        print(json.dumps({'run_id': str(result['id']), 'status': 'failed',
                          'failure_code': result.get('failure_code')}), flush=True)
        raise RuntimeError('Diagnostic run failed: ' + str(result.get('failure_code')))
    if result['status'] != 'succeeded':
        return False
    if not result.get('accepted_at') or not result.get('job_uid'):
        raise RuntimeError('Succeeded run has no acceptance or Job identity')
    artifacts = result['artifacts']
    if len(artifacts) != 1:
        raise RuntimeError('Expected exactly one diagnostic artifact')
    artifact = artifacts[0]
    if (artifact['bucket'] != bucket or artifact['size_bytes'] != len(EXPECTED)
            or not artifact['object_key'].endswith('/' + str(result['id']) + '/outputs/result.txt')):
        raise RuntimeError('Unexpected diagnostic artifact metadata')
    published = {event['event_type'] for event in result['events'] if event['published_at']}
    if not {'run.requested', 'run.succeeded'} <= published:
        return False
    response = storage.get_object(Bucket=bucket, Key=artifact['object_key'])
    try:
        if response['Body'].read(len(EXPECTED) + 1) != EXPECTED:
            raise RuntimeError('Diagnostic artifact bytes differ')
    finally:
        response['Body'].close()
    return True


def wait_for_run(inspect, storage, bucket, *, timeout=900, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    while clock() < deadline:
        result = inspect()
        if verify_result(result, storage, bucket):
            print(json.dumps(result, default=str), flush=True)
            return result
        sleep(min(5, max(0, deadline - clock())))
    raise TimeoutError('Execution verification timed out; the submitted run is retained')


def main():
    import boto3
    from botocore.config import Config
    from mic3_api.core.config import DatabaseSettings
    from mic3_api.infrastructure.database import Database
    from mic3_api.infrastructure.persistence.run_inspection import SqlAlchemyRunInspection

    database = None
    storage = None
    try:
        database = Database(DatabaseSettings().database_url, statement_timeout_ms=10000, lock_timeout_ms=3000)
        storage = boto3.client('s3', endpoint_url=os.environ['ARTIFACT_ENDPOINT'],
            aws_access_key_id=os.environ['ARTIFACT_ACCESS_KEY'],
            aws_secret_access_key=os.environ['ARTIFACT_SECRET_KEY'], region_name='us-east-1',
            config=Config(connect_timeout=3, read_timeout=10,
                retries={'total_max_attempts': 2, 'mode': 'standard'}, s3={'addressing_style': 'path'}))
        with database.open_session() as session:
            run_id = register_and_submit(session, os.environ['CHECK_MODEL_IMAGE'])
            store = SqlAlchemyRunInspection(session)
            wait_for_run(lambda: store.show(run_id), storage, os.environ['ARTIFACT_BUCKET'])
        return 0
    except Exception as exc:
        # SDK/SQL exception strings can contain connection details. Emit only type.
        print('Deployment verification failed: ' + type(exc).__name__, file=sys.stderr, flush=True)
        return 1
    finally:
        if storage is not None:
            storage.close()
        if database is not None:
            database.dispose()


if __name__ == '__main__':
    sys.exit(main())
