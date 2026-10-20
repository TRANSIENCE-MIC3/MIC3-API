#!/usr/bin/env bash
set -euo pipefail

# Explicit creation avoids misspelled topic names silently creating new streams.
for topic in mic3.run-requests.v1 mic3.run-events.v1; do
    /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:19092 \
        --create --if-not-exists --topic "$topic" \
        --partitions 1 --replication-factor 1 \
        --config retention.ms=604800000 \
        --config retention.bytes=1073741824 \
        --config segment.bytes=67108864 \
        --config cleanup.policy=delete
done
