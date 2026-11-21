#!/usr/bin/env bash
# Load-test scenarios of spec §10 against a running stack (`docker compose -f deploy/docker-compose.yml up -d`).
# Raw results: loadtest/results/<scenario>/{samples.csv,summary.json}, environment in results/env.txt,
# Prometheus snapshots in results/<scenario>/metrics-*.txt. Usage: loadtest/scenarios.sh [scenario ...]
# Scenarios: unique burst outage kill scale (default: all, in this order; scale stops delivery/executor).
set -uo pipefail
cd "$(dirname "$0")/.."
RATE=${RATE:-100}            # events/s for the latency scenarios
DURATION=${DURATION:-60}
BACKLOG=${BACKLOG:-20000}    # events per scaling run
OUT=loadtest/results
compose() { docker compose -f deploy/docker-compose.yml "$@"; }
psql_() { compose exec -T postgres psql -U "${POSTGRES_USER:-alerting}" -d "${POSTGRES_DB:-alerting}" -tAc "$1"; }
run() { uv run python loadtest/run.py --out "$OUT" "$@"; }
outage() { echo "curl -fsS -X POST localhost:8090/api/outage -H 'Content-Type: application/json' -d '{\"sms\": $1}'"; }
metrics() {  # snapshot of the processor and delivery metrics (ports are not published, read them inside)
  for svc in processor delivery; do
    compose exec -T "$svc" python -c "import urllib.request as u, os; print(u.urlopen('http://localhost:' + os.environ.get('METRICS_PORT', {'processor': '9101', 'delivery': '9102'}['$svc']) + '/metrics').read().decode())" \
      > "$OUT/$1/metrics-$svc.txt" 2>/dev/null || true
  done
}
failed=0
check() { [[ $1 == 0 ]] && echo "ok   $2" || { echo "FAIL $2"; failed=1; }; }

mkdir -p "$OUT"
{
  echo "date: $(date -u +%FT%TZ)"; echo "commit: $(git rev-parse --short HEAD)"
  echo "host: $(uname -srm)"; echo "cpus: $(nproc 2>/dev/null || sysctl -n hw.ncpu)"
  echo "memory: $(free -h 2>/dev/null | awk '/Mem:/ {print $2}')"
  echo "cpu model: $(lscpu 2>/dev/null | sed -n 's/^Model name:\s*//p')"
  echo "events.raw partitions: ${EVENTS_RAW_PARTITIONS:-12}"
} > "$OUT/env.txt"
cat "$OUT/env.txt"

# Throttling (default 5 SMS / 10 min per person) would hold almost every SMS of a load test: lift it.
# Restore the demo limits afterwards with `compose run --rm seed`.
psql_ "UPDATE channel_preferences SET limits = '{\"sms\": null, \"phone\": null, \"email\": null}'::jsonb" >/dev/null
run --name warmup --rate 20 --duration 5 >/dev/null

scenarios=("${@:-unique burst outage kill scale}")
for s in ${scenarios[*]}; do
  echo "=== $s"
  case $s in
    unique)  # unique events: one alert and one SMS per event
      run --name unique --rate "$RATE" --duration "$DURATION"; check $? "unique: delivered = expected"
      metrics unique ;;
    burst)   # duplicate burst: 20 copies of each event, one alert each (dedup)
      run --name burst --rate $((RATE * 5)) --duration 20 --dup 20; check $? "burst: delivered = expected"
      metrics burst ;;
    outage)  # SMS gateway down for 20 s under load: retries deliver everything afterwards
      run --name outage --rate "$RATE" --duration "$DURATION" --drain 300 \
        --at 15 "$(outage true)" --at 35 "$(outage false)"
      check $? "outage: delivered = expected after the SMS gateway came back"
      metrics outage ;;
    kill)    # kill one of two processors and one of two delivery workers under load
      compose up -d --scale processor=2 --scale delivery=2 --no-recreate processor delivery >/dev/null
      sleep 10
      run --name kill --rate "$RATE" --duration "$DURATION" --drain 300 \
        --at 20 "docker kill \$(docker compose -f deploy/docker-compose.yml ps -q processor | head -1)" \
        --at 22 "docker kill \$(docker compose -f deploy/docker-compose.yml ps -q delivery | head -1)" \
        --at 40 "docker compose -f deploy/docker-compose.yml up -d --scale processor=2 --scale delivery=2 processor delivery"
      check $? "kill: delivered = expected"
      metrics kill
      compose up -d --scale processor=1 --scale delivery=1 processor delivery >/dev/null ;;
    scale)   # processor throughput 1 -> 2 -> 4: drain a backlog of $BACKLOG events in events.raw
      compose stop delivery executor >/dev/null  # isolate the processor; notifications stay in the outbox
      echo "instances,events,seconds,eventsPerSecond" > "$OUT/scale.csv"
      for n in 1 2 4; do
        compose stop processor >/dev/null
        run --name "scale-$n" --via kafka --rate 0 --unique "$BACKLOG" --no-wait >/dev/null
        id=$(python3 -c "import json; print(json.load(open('$OUT/scale-$n/summary.json'))['run'])")
        count() { psql_ "SELECT count(*) FROM processed_events WHERE event_id LIKE 'lt:LT-$id-%'"; }
        compose up -d --scale processor=$n processor >/dev/null
        until [[ $(count) -gt 0 ]]; do sleep 0.2; done
        t0=$(date +%s.%N)
        for _ in $(seq 1 1200); do [[ $(count) -ge $BACKLOG ]] && break; sleep 0.5; done
        [[ $(count) -ge $BACKLOG ]] || { echo "FAIL scale-$n: backlog not drained in 10 min"; failed=1; }
        t1=$(date +%s.%N)
        python3 -c "s=$t1-$t0; print(f'$n,$BACKLOG,{s:.1f},{$BACKLOG/s:.0f}')" | tee -a "$OUT/scale.csv"
        metrics "scale-$n"
      done
      compose up -d --scale processor=1 processor delivery executor >/dev/null ;;
    *) echo "unknown scenario $s"; failed=1 ;;
  esac
done
exit $failed
