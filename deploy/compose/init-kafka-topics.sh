#!/usr/bin/env bash
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"
KAFKA_TOPICS_BIN="/opt/kafka/bin/kafka-topics.sh"

TOPICS=(
  billing.subscription.events
  billing.invoice.events
  billing.ledger.events
  billing.reconciliation.events
  billing.entitlement.events
  billing.dlq
)

echo "Waiting for Kafka at ${BOOTSTRAP}..."
for _ in $(seq 1 30); do
  if "${KAFKA_TOPICS_BIN}" --bootstrap-server "${BOOTSTRAP}" --list >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

for topic in "${TOPICS[@]}"; do
  echo "Ensuring topic: ${topic}"
  "${KAFKA_TOPICS_BIN}" \
    --bootstrap-server "${BOOTSTRAP}" \
    --create \
    --if-not-exists \
    --topic "${topic}" \
    --partitions 1 \
    --replication-factor 1
done

echo "Kafka topics initialized."
