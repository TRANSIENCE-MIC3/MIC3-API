# Local Kafka checkpoint

This is commit 1 of the execution integration. Kafka is available locally;
submission still only writes PostgreSQL. No publisher or worker is running yet.

The official JVM image is pinned to Apache Kafka 4.1.1 and its registry digest.
This development broker uses plaintext on the Docker network and a localhost-only
host port. EOSC will use separately configured authentication and TLS.

## Start and inspect

From the repository root, prepare `.env` using the main README, then run each
PowerShell command separately and stop if a command fails:

```powershell
docker compose up -d --wait kafka
docker compose run --rm kafka-init
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:19092 --describe
```

A **broker** stores and serves records. **KRaft** manages Kafka cluster metadata
without ZooKeeper; this one process has both broker and controller roles.
A **topic** is a named event stream. Each of our two topics has one **partition**,
an ordered log, and one replica: there is no redundant copy on another broker.

Kafka topic and consumer-group names use lowercase words separated only by periods.
Kafka may still print its generic period/underscore metric warning for these names;
we avoid the collision by never using underscores in topic names.

The initializer explicitly creates `mic3.run.requests.v1` and
`mic3.run.events.v1`. Running it again leaves existing topics and settings intact;
it does not reconcile configuration changes. Automatic topic creation is disabled.
Each topic retains data for up to seven days or approximately 1GiB per partition,
whichever limit removes old segments first. Retention is asynchronous and
segment-based, not a strict disk quota. Local Docker volume capacity is managed
by Docker; the later EOSC persistent claim will have an explicit size.

**Listeners** are broker connection endpoints. Host Python processes use
`localhost:9092` (or `KAFKA_PORT` from `.env`); Docker clients use `kafka:19092`.
Kafka returns these **advertised addresses** to clients after connecting, so they
must be reachable from that client's network. The controller port is not published.

## Write, read, and replay

Use a separate learning topic so demonstration records cannot later launch work:

```powershell
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:19092 --create --if-not-exists --topic mic3.learning --partitions 1 --replication-factor 1 --config retention.ms=86400000 --config retention.bytes=16777216
'demo-run:hello-kafka' | docker compose exec -T kafka /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server kafka:19092 --topic mic3.learning --property parse.key=true --property key.separator=: --producer-property acks=all
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:19092 --topic mic3.learning --group learning.reader.a --from-beginning --max-messages 1 --timeout-ms 20000 --property print.key=true
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:19092 --topic mic3.learning --group learning.reader.b --from-beginning --max-messages 1 --timeout-ms 20000 --property print.key=true
```

A **record key** (`demo-run`) determines partition placement; our real events will
use the run UUID. Ordering is per partition. `acks=all` requests acknowledgement
from all current in-sync replicas; on this single-node setup that means one copy.

A **consumer group** has its own reading position. Both independent groups above
can read the same record: consumption does not delete it. An **offset** identifies
a position in a partition; a group's committed offset is its next reading position.
Console consumers use automatic commits; the future worker will commit explicitly
only after durable database acceptance.

```powershell
docker compose exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:19092 --describe --group learning.reader.a
docker compose restart kafka
docker compose up -d --wait kafka
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:19092 --topic mic3.learning --group learning.reader.after.restart --from-beginning --max-messages 1 --timeout-ms 20000 --property print.key=true
```

Expect `demo-run` and `hello-kafka` again after restart. On repeated walkthroughs,
use new group names to replay old records; `--from-beginning` does not override a
group's existing committed offset. A short console run may exit before its
periodic offset commit, so the group description may not yet show a position.

The named `mic3-kafka-data` volume holds records and KRaft metadata. Its fixed local
cluster ID is not a secret and must remain unchanged with that data. The upstream
image initializes unformatted storage and retains already formatted storage.
Do not delete `meta.properties`, change the cluster ID, or remove the volume to
resolve a startup problem; inspect `docker compose logs kafka` first.

Stop Kafka with `docker compose stop kafka`. Avoid `docker compose down -v`:
it removes this project's database and identity-provider volumes as well.
One broker is unavailable during restarts and cannot survive loss of its volume.

## Automated checkpoint

With the project Python environment activated and Docker running:

```powershell
python -m pytest tests/integration/infrastructure/test_local_kafka.py -v
```

This test uses a uniquely named Compose project, an available host port and an
isolated volume. It checks repeated topic initialization, keyed publication,
independent readers, and replay after replacing the broker container with the
same volume and metadata. It removes only its own disposable project afterward.
The first run may need to download the pinned image.

References: [Apache Docker example](https://github.com/apache/kafka/blob/4.1.1/docker/examples/docker-compose-files/single-node/plaintext/docker-compose.yml)
and [Kafka design](https://kafka.apache.org/41/design/design/).
