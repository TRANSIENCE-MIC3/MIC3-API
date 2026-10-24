#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /tmp/kafka /var/lib/kafka/data/logs
cat /var/run/mic3/kafka/tls.key /var/run/mic3/kafka/tls.crt > /tmp/kafka/identity.pem
metadata=/var/lib/kafka/data/logs/meta.properties
if [ -f "$metadata" ]; then
    grep -Fxq "cluster.id=$CLUSTER_ID" "$metadata" || { echo 'Cluster identity differs from retained storage.' >&2; exit 1; }
    grep -Fxq 'node.id=1' "$metadata" || { echo 'Node identity differs from retained storage.' >&2; exit 1; }
else
    # Never format a directory containing unexplained data or partial metadata.
    if [ -n "$(find /var/lib/kafka/data/logs -mindepth 1 -maxdepth 1 -print -quit)" ]; then
        echo 'Nonempty Kafka storage has no metadata; explicit recovery required.' >&2
        exit 1
    fi
    /opt/kafka/bin/kafka-storage.sh format --cluster-id "$CLUSTER_ID" --config /etc/mic3/kafka/server.properties
fi
exec /opt/kafka/bin/kafka-server-start.sh /etc/mic3/kafka/server.properties
