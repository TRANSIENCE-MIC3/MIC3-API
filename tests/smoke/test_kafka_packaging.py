"""Opt-in Docker qualification of the rendered chart and packaged Kafka clients.

No Kubernetes credentials are read. Fixtures create their own CA, network and
volumes, then remove only those resources. EOSC certificates are not generated.
"""
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import subprocess
import tarfile
from uuid import uuid4

import docker
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import pytest
import yaml


pytestmark = [pytest.mark.smoke, pytest.mark.skipif(
    os.getenv("MIC3_KAFKA_PACKAGING_SMOKE") != "1", reason="Opt-in Docker Kafka packaging check")]
ROOT = Path(__file__).resolve().parents[2]
CLIENT_IMAGE = os.getenv("MIC3_KAFKA_CLIENT_IMAGE", "mic3-worker:packaging-check")


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, data in files.items():
            data = data.encode() if isinstance(data, str) else data
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.gid = len(data), 0o640, 0
            tar.addfile(entry, io.BytesIO(data))
    return stream.getvalue()


def make_ca():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "packaging-test-ca")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(key, hashes.SHA256()))
    return key, cert


def identity(role, ca_key, ca_cert, dns=()):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"mic3-kafka-{role}")]))
               .issuer_name(ca_cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
               .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH] + ([ExtendedKeyUsageOID.SERVER_AUTH] if dns else [])), critical=False))
    if dns:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(name) for name in dns]), critical=False)
    return {"tls.crt": builder.sign(ca_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM),
            "tls.key": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())}


PROBE = '''
import os, time
from datetime import datetime, timezone
from uuid import UUID
from confluent_kafka import Consumer, Producer, KafkaException, TopicPartition, KafkaError
from confluent_kafka.admin import AdminClient, NewTopic
from mic3_api.application.runs.publication import RunEvent, PublicationError
from mic3_api.core.publisher_config import PublisherSettings
from mic3_api.infrastructure.kafka_publication import KafkaEventPublisher
from mic3_api.infrastructure.kafka_consumption import KafkaRequestConsumer
from mic3_api.infrastructure.kafka_security import security_config
mode = os.environ['MODE']
address = os.environ['ADDRESS']
settings = PublisherSettings(db_host='unused', db_name='test', db_user='test', db_password='test', _env_file=None,
    kafka_security_protocol='SSL', kafka_ssl_ca_file='/var/run/mic3/kafka-trust/ca.crt',
    kafka_ssl_certificate_file='/var/run/mic3/kafka/tls.crt', kafka_ssl_key_file='/var/run/mic3/kafka/tls.key')
config = dict(security_config(settings), **{'bootstrap.servers':address, 'log_level':0})
request, events, group = 'mic3.run.requests.v1', 'mic3.run.events.v1', 'mic3.execution.worker.v1'
event = RunEvent(UUID('12345678-1234-1234-1234-123456789abc'), UUID('22345678-1234-1234-1234-123456789abc'),
                 'run.requested', datetime.now(timezone.utc))
if mode in ('no-certificate', 'untrusted', 'wrong-host'):
    if mode == 'no-certificate':
        config.pop('ssl.certificate.location'); config.pop('ssl.key.location')
    try:
        AdminClient(config).list_topics(timeout=4)
    except KafkaException:
        print('TLS connection rejected:', mode)
    else:
        raise AssertionError('Invalid TLS connection accepted')
elif mode in ('publish', 'deny-publish'):
    publisher = KafkaEventPublisher(address, request, 3, events, security=settings)
    try:
        publisher.publish(event)
        if mode == 'deny-publish': raise AssertionError('Unauthorized publication accepted')
        publisher.publish(RunEvent(event.id, event.run_id, 'run.succeeded', event.created_at))
    except PublicationError as error:
        if mode != 'deny-publish': raise
        if isinstance(error.__cause__, KafkaException):
            assert error.__cause__.args[0].code() in (KafkaError.TOPIC_AUTHORIZATION_FAILED, KafkaError.CLUSTER_AUTHORIZATION_FAILED)
        else:
            assert 'TOPIC_AUTHORIZATION_FAILED' in str(error)
    finally:
        publisher.close()
    if mode == 'deny-publish':
        # Also exercise topic ACLs without the earlier idempotent-write check.
        plain_producer = Producer(dict(config, **{'enable.idempotence':False, 'message.timeout.ms':3000}))
        delivery_errors = []
        plain_producer.produce(request, value=b'forbidden', on_delivery=lambda error, message: delivery_errors.append(error))
        plain_producer.flush(5)
        assert len(delivery_errors) == 1
        assert delivery_errors[0].code() == KafkaError.TOPIC_AUTHORIZATION_FAILED
    print(mode, 'passed')
elif mode == 'consume':
    consumer = KafkaRequestConsumer(address, request, group, security=settings)
    try:
        deadline = time.monotonic() + 20
        record = None
        while record is None and time.monotonic() < deadline: record = consumer.poll(0.5)
        assert record is not None and str(event.id).encode() in record.value
        consumer.commit(record)
    finally:
        consumer.close()
    print('Packaged worker consumed and committed')
elif mode == 'replay':
    consumer = Consumer(dict(config, **{'group.id':group, 'enable.auto.commit':False}))
    try:
        consumer.assign([TopicPartition(request, 0, 0)])
        record = consumer.poll(15)
        assert record is not None and record.error() is None and str(event.id).encode() in record.value()
    finally:
        consumer.close()
    print('Retained record replayed')
elif mode in ('deny-topic', 'deny-group'):
    consumer = Consumer(dict(config, **{'group.id':group if mode == 'deny-topic' else 'forbidden.group', 'enable.auto.commit':False}))
    try:
        consumer.subscribe([events if mode == 'deny-topic' else request])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            record = consumer.poll(0.5)
            if record is not None:
                assert record.error() is not None
                assert record.error().code() in (KafkaError.TOPIC_AUTHORIZATION_FAILED, KafkaError.GROUP_AUTHORIZATION_FAILED)
                break
        else: raise AssertionError('No authorization rejection received')
    finally:
        consumer.close()
    print(mode, 'passed')
elif mode == 'deny-create':
    admin = AdminClient(config)
    future = admin.create_topics([NewTopic('forbidden.topic', num_partitions=1, replication_factor=1)], request_timeout=10)['forbidden.topic']
    try: future.result(timeout=15)
    except KafkaException as error:
        assert error.args[0].code() in (KafkaError.TOPIC_AUTHORIZATION_FAILED, KafkaError.CLUSTER_AUTHORIZATION_FAILED)
    else: raise AssertionError('Unauthorized topic creation accepted')
    print('Topic creation denied')
'''


def test_secured_chart_persistence_and_client_permissions():
    client = docker.from_env()
    client.images.get(CLIENT_IMAGE)
    docs = list(yaml.safe_load_all(subprocess.check_output([
        'helm', 'template', 'test', str(ROOT / 'deploy/helm/kafka'), '--namespace', 'sandbox',
        '--set', 'broker.enabled=true', '--set', 'broker.clusterId=MkMza2Fma2Fsb2NhbDAwMQ'], text=True)))
    config = next(doc['data'] for doc in docs if doc and doc['kind'] == 'ConfigMap')
    broker_spec = next(doc['spec']['template']['spec']['containers'][0] for doc in docs if doc and doc['kind'] == 'StatefulSet')
    init_spec = next(doc['spec']['template']['spec']['containers'][0] for doc in docs if doc and doc['kind'] == 'Job')
    image = broker_spec['image']
    client.images.get(image)
    suffix = uuid4().hex[:12]
    network = client.networks.create('mic3-kafka-qualification-' + suffix)
    volumes, containers = {}, []
    address = 'mic3-kafka.sandbox.svc.cluster.local'
    controller = 'mic3-kafka-0.mic3-kafka-headless.sandbox.svc.cluster.local'
    try:
        for name in ('config', 'data', 'dirty-data', 'trust', 'broker', 'operator', 'publisher', 'worker', 'unknown', 'untrusted'):
            volumes[name] = client.volumes.create('mic3-kafka-qualification-' + suffix + '-' + name)
        mounts = {volume.name: {'bind':'/fixtures/' + name, 'mode':'rw'} for name, volume in volumes.items()}
        helper = client.containers.run(image, entrypoint=['/bin/sleep'], command=['infinity'],
                                       user='0', volumes=mounts, network=network.name, detach=True)
        containers.append(helper)
        ca_key, ca_cert = make_ca()
        for role in ('broker', 'operator', 'publisher', 'worker', 'unknown'):
            helper.put_archive('/fixtures/' + role, archive(identity(role, ca_key, ca_cert, (address, controller) if role == 'broker' else ())))
        other_key, other_ca = make_ca()
        helper.put_archive('/fixtures/untrusted', archive(identity('publisher', other_key, other_ca)))
        helper.put_archive('/fixtures/trust', archive({'ca.crt':ca_cert.public_bytes(serialization.Encoding.PEM)}))
        helper.put_archive('/fixtures/config', archive(config))
        helper.put_archive('/fixtures/dirty-data', archive({'logs/unexplained-record':'do not format'}))
        result = helper.exec_run(['chmod', '-R', '0770', '/fixtures/data', '/fixtures/dirty-data'])
        assert result.exit_code == 0

        def mounts_for(role, data=None):
            chosen = {'config':'/etc/mic3/kafka', 'trust':'/var/run/mic3/kafka-trust', role:'/var/run/mic3/kafka'}
            mounts = {volumes[key].name:{'bind':path,'mode':'ro'} for key,path in chosen.items()}
            if data: mounts[volumes[data].name] = {'bind':'/var/lib/kafka/data','mode':'rw'}
            return mounts

        def start_broker(cluster_id=None, data='data'):
            environment = {item['name']:item['value'] for item in broker_spec['env']}
            if cluster_id: environment['CLUSTER_ID'] = cluster_id
            broker = client.containers.create(image, entrypoint=broker_spec['command'],
                environment=environment, user='1001230000:0', read_only=True, tmpfs={'/tmp':'rw,size=256m,mode=1777'},
                volumes=mounts_for('broker', data), mem_limit='2g', cap_drop=['ALL'], security_opt=['no-new-privileges'])
            containers.append(broker)
            network.connect(broker, aliases=[address, controller, 'wrong-server'])
            broker.start()
            return broker

        def initialize():
            job = client.containers.run(image, entrypoint=init_spec['command'],
                environment={item['name']:item['value'] for item in init_spec['env']},
                user='1001230000:0', read_only=True, tmpfs={'/tmp':'rw,size=64m,mode=1777'},
                volumes=mounts_for('operator'), network=network.name, detach=True, mem_limit='512m', cap_drop=['ALL'])
            containers.append(job)
            result = job.wait(timeout=300)
            assert result['StatusCode'] == 0, job.logs().decode()

        def probe(role, mode, host=address):
            test = client.containers.run(CLIENT_IMAGE, entrypoint=['python','-c',PROBE],
                environment={'MODE':mode, 'ADDRESS':host + ':9092'},
                volumes=mounts_for(role), network=network.name, user='1001230000:0',
                read_only=True, tmpfs={'/tmp':'rw,size=32m,mode=1777'}, detach=True)
            containers.append(test)
            result = test.wait(timeout=60)
            assert result['StatusCode'] == 0, test.logs().decode()

        dirty = start_broker(data='dirty-data')
        assert dirty.wait(timeout=30)['StatusCode'] != 0
        assert b'Nonempty Kafka storage has no metadata' in dirty.logs()
        dirty.remove()
        broker = start_broker()
        initialize()
        probe('publisher', 'publish')
        initialize()  # Idempotent bootstrap must retain existing records.
        probe('worker', 'consume')
        probe('worker', 'deny-publish')
        probe('unknown', 'deny-publish')
        probe('worker', 'deny-topic')
        probe('worker', 'deny-group')
        probe('publisher', 'deny-create')
        probe('publisher', 'no-certificate')
        probe('untrusted', 'untrusted')
        probe('publisher', 'wrong-host', 'wrong-server')
        before = broker.exec_run(['cat','/var/lib/kafka/data/logs/meta.properties']).output
        broker.stop(timeout=60)
        assert broker.wait()['StatusCode'] in (0, 143)
        shutdown_log = broker.logs()
        assert b'Shutdown completed' in shutdown_log and b'App info kafka.server for 1 unregistered' in shutdown_log, broker.logs(tail=30).decode()
        wrong = start_broker('AAAAAAAAAAAAAAAAAAAAAA')
        assert wrong.wait(timeout=30)['StatusCode'] != 0
        assert b'Cluster identity differs' in wrong.logs()
        wrong.remove()
        broker.remove()
        broker = start_broker()
        initialize()
        after = broker.exec_run(['cat','/var/lib/kafka/data/logs/meta.properties']).output
        assert before == after
        probe('worker', 'replay')
        print('mTLS, ACL denials, repeated bootstrap, arbitrary UID, clean shutdown, identity guard and retained-record replay passed')
    finally:
        for container in reversed(containers):
            try: container.remove(force=True)
            except docker.errors.NotFound: pass
        network.remove()
        for volume in volumes.values(): volume.remove()
        client.close()
