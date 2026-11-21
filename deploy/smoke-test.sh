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

for job in migrate redpanda-init; do
  code=$(docker inspect -f '{{.State.ExitCode}}' "$(compose ps -aq "$job")")
  [[ "$code" == 0 ]] && echo "ok   $job exited 0" || { echo "FAIL $job exited $code"; exit 1; }
done

compose exec -T redpanda rpk topic list -X brokers=redpanda:9092 | grep -qw 'events.raw' \
  && echo "ok   topic events.raw" || { echo "FAIL topic events.raw missing"; exit 1; }

for svc in processor delivery executor; do
  state=$(docker inspect -f '{{.State.Status}}' "$(compose ps -q "$svc")")
  [[ "$state" == running ]] && echo "ok   $svc running" || { echo "FAIL $svc is $state"; exit 1; }
done
echo "smoke test passed"
