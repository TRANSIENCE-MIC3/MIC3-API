#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /tmp/kafka
cat /var/run/mic3/kafka/tls.key /var/run/mic3/kafka/tls.crt > /tmp/kafka/identity.pem
common=(--bootstrap-server "$BOOTSTRAP_SERVERS" --command-config /etc/mic3/kafka/client.properties)
# Readiness is TCP-only; authenticated readiness is established here.
ready=false
for attempt in {1..12}; do
    if /opt/kafka/bin/kafka-topics.sh "${common[@]}" --list; then ready=true; break; fi
    sleep 5
done
$ready || { echo 'Authenticated Kafka startup did not complete.' >&2; exit 1; }
for topic in "$REQUESTS_TOPIC" "$EVENTS_TOPIC"; do
    /opt/kafka/bin/kafka-topics.sh "${common[@]}" --create --if-not-exists \
        --topic "$topic" --partitions 1 --replication-factor 1 \
        --config "retention.ms=$RETENTION_MS" --config "retention.bytes=$RETENTION_BYTES" \
        --config "segment.bytes=$SEGMENT_BYTES"
    # Existing topics keep their records; update only the managed retention settings.
    description=$(/opt/kafka/bin/kafka-topics.sh "${common[@]}" --describe --topic "$topic")
    [[ "$description" =~ PartitionCount:[[:space:]]+1([[:space:]]|$) && "$description" =~ ReplicationFactor:[[:space:]]+1([[:space:]]|$) ]] || {
        echo 'Existing topic topology differs from the single-node contract.' >&2; exit 1;
    }
    /opt/kafka/bin/kafka-configs.sh "${common[@]}" --alter --entity-type topics --entity-name "$topic" \
        --add-config "retention.ms=$RETENTION_MS,retention.bytes=$RETENTION_BYTES,segment.bytes=$SEGMENT_BYTES"
    /opt/kafka/bin/kafka-acls.sh "${common[@]}" --add --allow-principal "User:CN=$PUBLISHER_IDENTITY" \
        --operation Write --operation Describe --topic "$topic"
done
/opt/kafka/bin/kafka-acls.sh "${common[@]}" --add --allow-principal "User:CN=$PUBLISHER_IDENTITY" \
    --operation IdempotentWrite --cluster
/opt/kafka/bin/kafka-acls.sh "${common[@]}" --add --allow-principal "User:CN=$WORKER_IDENTITY" \
    --operation Read --operation Describe --topic "$REQUESTS_TOPIC"
/opt/kafka/bin/kafka-acls.sh "${common[@]}" --add --allow-principal "User:CN=$WORKER_IDENTITY" \
    --operation Read --group "$CONSUMER_GROUP"
echo 'Kafka topics and application ACLs initialized.'
