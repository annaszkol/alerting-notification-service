#!/usr/bin/env bash
# Video demo (spec §11 script) against a running stack. Each scene sets up the mocks, sends the events and says what
# to show on screen. Usage: deploy/demo.sh [scene ...]   scenes: events burst outage runbook unknown (default: all)
# Set NO_PAUSE=1 to run without waiting for Enter between steps (CI / rehearsal).
# SMS throttling (5 / 10 min per person) still applies: wait 10 min between full rehearsals on one stack.
set -euo pipefail
cd "$(dirname "$0")/.."
GATEWAY=${GATEWAY_URL:-http://localhost:8090}
TARGETS=${TARGETS_URL:-http://localhost:8091}
compose() { docker compose -f deploy/docker-compose.yml "$@"; }
gen() { compose exec -T -e INGEST_URL=http://ingest:8080 tailer python -m mocks.generator "$@"; }
fail() { printf '\n\033[31mFAIL %s\033[0m\n' "$*"; exit 1; }
say() { printf '\n\033[1m>>> %s\033[0m\n' "$*"; }
pause() { [[ -n ${NO_PAUSE:-} ]] || read -rp "    [Enter] " _; }
sms_count() { curl -fsS "$GATEWAY/api/messages?channel=sms&direction=out" | python3 -c 'import sys, json; print(len(json.load(sys.stdin)))'; }

scene_events() {  # 0:50-1:40 three sample events + pricing logs (tests 1-4)
  say "Workflow failure: Settlements on-call gets a phone call and an SMS (fake gateway $GATEWAY)"
  gen workflow; pause
  say "Two pricing engines log WARN for one instrument: one alert, live counter, no SMS or email"
  gen pricing --lines 2 >/dev/null; pause
  say "Rejected trade over 10M: email to Rates (Mailpit http://localhost:8025)"
  gen trade --notional 25000000; pause
  say "payments-api critical: SMS to Anna between 08:00 and 20:00 Warsaw time, email otherwise; Markdown runbook on the alert"
  gen high-error-rate; pause
}

scene_burst() {  # 1:40-2:05 500 identical errors -> one alert with count 500 (test 8)
  say "500 identical workflow failures: one alert in the UI with count 500, one series of notifications"
  gen burst --count 500; pause
}

scene_outage() {  # 2:05-2:30 SMS outage -> retry -> nothing lost (test 10)
  say "SMS gateway outage ON (toggle visible at $GATEWAY)"
  curl -fsS -X POST "$GATEWAY/api/outage" -H 'Content-Type: application/json' -d '{"sms": true}' >/dev/null
  before=$(sms_count)
  gen workflow
  say "Alert created, SMS fails and is retried with backoff (delivery logs). Waiting 20 s..."
  sleep 20
  curl -fsS -X POST "$GATEWAY/api/outage" -H 'Content-Type: application/json' -d '{"sms": false}' >/dev/null
  say "Outage OFF, waiting for the retry"
  for _ in $(seq 1 60); do [[ $(sms_count) -gt $before ]] && break; sleep 1; done
  [[ $(sms_count) -gt $before ]] || fail "no SMS after the outage (compose logs delivery)"
  say "SMS delivered after the outage: 0 lost"
  pause
}

scene_runbook() {  # 3:15-4:05 YAML runbook: health check, two failed retries, escalation approved by SMS (test 12)
  say "Workflow engine mock will fail the next 2 retries; the settlement-timeout runbook starts on the new alert"
  curl -fsS -X POST "$TARGETS/api/fail" -H 'Content-Type: application/json' -d '{"target": "workflow", "times": 2}' >/dev/null
  gen workflow
  say "Show the alert's runbook timeline: health check ok, retry failed twice, escalation waits for approval"
  for _ in $(seq 1 90); do
    approval=$(curl -fsS "$GATEWAY/api/messages?channel=sms&direction=out" | python3 -c '
import sys, json
m = [m for m in json.load(sys.stdin) if "approval requested" in m["text"] and m.get("replyCode")]
print(m[-1]["to"], m[-1]["replyCode"]) if m else print()')
    [[ -n $approval ]] && break
    sleep 2
  done
  [[ -n $approval ]] || fail "no approval SMS (compose logs executor delivery)"
  read -r phone code <<<"$approval"
  say "Approval SMS went to $phone. Replying APPROVE $code from that phone (or use the reply form in the gateway page)"
  pause
  answer=$(curl -fsS -X POST "$GATEWAY/api/reply" -H 'Content-Type: application/json' \
    -d "{\"from\": \"$phone\", \"text\": \"APPROVE $code\"}")
  echo "    $answer"
  [[ $answer == *approved* ]] || fail "approval by SMS was not accepted"
  say "Escalation runs; the timeline shows who approved it. Then tick the Markdown/checklist steps in the UI (test 13)"
  pause
}

scene_unknown() {  # 2:30-3:15 new schema -> Unmapped -> mapping + rule in the UI (test 11)
  say "An event no mapping recognises: it shows up under 'Unmapped'. Create the mapping and a rule in the UI (stopwatch)"
  gen unknown; pause
  say "Send it again after saving the rule: it becomes an alert"
  pause; gen unknown
}

scenes=("${@:-events burst outage runbook unknown}")
for s in ${scenes[*]}; do
  declare -F "scene_$s" >/dev/null || { echo "unknown scene $s (events burst outage runbook unknown)"; exit 1; }
  "scene_$s"
done
say "Done."
