#!/usr/bin/env bash
# Checks that a running `docker compose up` stack is healthy. Used by CI; handy locally too.
set -euo pipefail
cd "$(dirname "$0")"
compose() { docker compose -f docker-compose.yml "$@"; }

wait_for() {
  local name=$1 url=$2
  for _ in $(seq 1 90); do
    if curl -fsS "$url" >/dev/null 2>&1; then echo "ok   $name"; return 0; fi
    sleep 2
  done
  echo "FAIL $name ($url)"; return 1
}

wait_for api http://localhost:8000/healthz
wait_for ingest http://localhost:8080/healthz
wait_for mailpit http://localhost:8025/livez
wait_for fake-gateway http://localhost:8090/healthz
wait_for action-targets http://localhost:8091/healthz
wait_for web http://localhost:3000/healthz
wait_for web-api-proxy http://localhost:3000/api/auth/users

for job in migrate seed redpanda-init; do
  code=$(docker inspect -f '{{.State.ExitCode}}' "$(compose ps -aq "$job")")
  [[ "$code" == 0 ]] && echo "ok   $job exited 0" || { echo "FAIL $job exited $code"; exit 1; }
done

compose exec -T redpanda rpk topic list -X brokers=redpanda:9092 | grep -qw 'events.raw' \
  && echo "ok   topic events.raw" || { echo "FAIL topic events.raw missing"; exit 1; }

users=$(compose exec -T postgres psql -U "${POSTGRES_USER:-alerting}" -d "${POSTGRES_DB:-alerting}" -tAc "SELECT count(*) FROM users")
[[ "$users" -gt 0 ]] && echo "ok   seed: $users users" || { echo "FAIL seed: no users"; exit 1; }

for svc in processor delivery executor tailer kafka-adapter; do
  state=$(docker inspect -f '{{.State.Status}}' "$(compose ps -q "$svc")")
  [[ "$state" == running ]] && echo "ok   $svc running" || { echo "FAIL $svc is $state"; exit 1; }
done
# End to end (spec §13 test 1): event over HTTP -> Kafka -> processor -> outbox -> delivery -> fake SMS gateway.
token="${SEED_INGEST_TOKEN:-dev-ingest-token}"
key="smoke-$(date +%s)-$RANDOM"
workflow_id="WF-SMOKE-$RANDOM"
curl -fsS -X POST http://localhost:8080/ingest/workflows -H "X-Source-Token: $token" -H "Idempotency-Key: $key" \
  -H 'Content-Type: application/json' -d @- >/dev/null <<JSON
{"type": "WORKFLOW_FAILED", "workflowId": "$workflow_id", "workflowName": "order-settlement",
 "step": "validate-counterparty", "severity": "ERROR", "environment": "prod",
 "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
 "error": {"code": "TIMEOUT", "message": "Counterparty service did not respond in 5000 ms"}}
JSON
echo "ok   event accepted by ingest"
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8090/api/messages?channel=sms | grep -q "Settlement failed: $workflow_id"; then
    echo "ok   SMS for $workflow_id reached the fake gateway (end to end)"; break
  fi
  sleep 1
done
curl -fsS http://localhost:8090/api/messages?channel=sms | grep -q "Settlement failed: $workflow_id" \
  || { echo "FAIL no SMS for $workflow_id"; compose logs --no-color processor delivery ingest | tail -60; exit 1; }

# Kafka adapter: a workflow failure on the external topic workflow-events -> SMS.
kafka_wf="WF-KAFKA-$RANDOM"
printf '{"type": "WORKFLOW_FAILED", "workflowId": "%s", "workflowName": "order-settlement", "step": "book", "severity": "ERROR", "environment": "prod", "error": {"code": "TIMEOUT", "message": "slow"}}\n' "$kafka_wf" \
  | compose exec -T redpanda rpk topic produce workflow-events -X brokers=redpanda:9092 >/dev/null
for _ in $(seq 1 60); do
  curl -fsS "http://localhost:8090/api/messages?channel=sms" | grep -q "Settlement failed: $kafka_wf" && break
  sleep 1
done
if curl -fsS "http://localhost:8090/api/messages?channel=sms" | grep -q "Settlement failed: $kafka_wf"; then
  echo "ok   Kafka source -> SMS for $kafka_wf"
else
  echo "FAIL no SMS for $kafka_wf"; compose logs --no-color kafka-adapter processor | tail -40; exit 1
fi

# Log tailer (spec §13 test 2): two WARN lines from two pricing engines -> one pricing alert in the API.
compose exec -T tailer python -m mocks.generator pricing --lines 2 >/dev/null
api_token=$(curl -fsS -X POST localhost:8000/api/auth/login -H 'Content-Type: application/json' -d '{"userId": "grace"}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["token"])')
for _ in $(seq 1 60); do
  curl -fsS "localhost:8000/api/alerts?team=pricing" -H "Authorization: Bearer $api_token" | grep -q "EURUSD" && break
  sleep 1
done
if curl -fsS "localhost:8000/api/alerts?team=pricing" -H "Authorization: Bearer $api_token" | grep -q "EURUSD"; then
  echo "ok   log lines -> pricing alert in the API"
else
  echo "FAIL no pricing alert"; compose logs --no-color tailer processor | tail -40; exit 1
fi

echo "smoke test passed"
