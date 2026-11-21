"""Spec §3 mappings, §4 rules and sample events (Appendix A shapes), shared by tests of the library and the processor."""

from __future__ import annotations

from datetime import UTC, datetime

import yaml

MAPPINGS = yaml.safe_load(
    """
- name: workflow-event
  priority: 10
  detect: { all: [ { type: { exists: true } }, { workflowId: { exists: true } } ] }
  fields:
    $schema: { const: workflow }
    $service: workflowName
    $environment: environment
    $severity: { from: severity, map: { INFO: info, WARN: warning, ERROR: error, CRITICAL: critical } }
    $eventTime: { from: timestamp, format: iso8601 }
    $summary: "{{workflowName}} / {{step}}: {{error.message}}"
    $correlationKey: "{{workflowId}}"

- name: metric-alert
  priority: 20
  detect: { any: [ { alertname: { exists: true } }, { alerts: { exists: true } } ] }
  splitOn: alerts
  fields:
    $schema: { const: metric-alert }
    $service: labels.service
    $instance: labels.instance
    $environment: labels.environment
    $severity: { from: labels.severity, map: { info: info, warning: warning, critical: critical } }
    $status: { from: status, map: { firing: firing, resolved: resolved } }
    $eventTime: { from: startsAt, format: iso8601 }
    $summary: "{{alertname}}: {{annotations.summary}}"
    $correlationKey: "{{alertname}}|{{labels.service}}|{{labels.region}}"

- name: business-event
  priority: 30
  detect: { meta.schema: { exists: true } }
  schemaFrom: meta.schema
  fields:
    $service: meta.producer
    $eventTime: { from: meta.ts, format: epoch_ms }
    $summary: "Trade {{payload.tradeId}} rejected: {{payload.reason}}"
    $correlationKey: "{{payload.tradeId}}"

- name: pricing-log
  priority: 40
  sources: [pricing-logs]
  detect: { level: { exists: true } }
  fields:
    $service: service
    $severity: { from: level, map: { DEBUG: info, INFO: info, WARN: warning, ERROR: error } }
    $eventTime: { from: ts, format: iso8601 }
    $summary: "{{service}} [{{instrument}}]: {{msg}}"
"""
)

RULES = yaml.safe_load(
    """
- id: settlements-prod-failures
  owner: { team: settlements }
  when: { all: [ { type: WORKFLOW_FAILED }, { workflowName: order-settlement }, { $environment: prod } ] }
  route: [ { to: { oncall: settlements }, channels: [phone, sms] } ]
  present: { title: "Settlement failed: {{workflowId}}", body: "Step {{step}}: {{error.message}}", fields: [step, error.message], color: red }
  dedup: { key: [workflowId], mode: until_resolved }

- id: pricing-warnings
  owner: { team: pricing }
  when: { all: [ { $service: { glob: "pricing-engine-*" } }, { $severity: { gte: warning } } ] }
  route: []
  present: { title: "{{instrument}}: {{$count}} ostrzeżeń z pricing-engine", fields: [$service, msg] }
  dedup: { key: [instrument], mode: window, window: 5m }
  autoResolveAfter: 30m

- id: rates-large-rejections
  owner: { team: rates }
  when: { all: [ { $schema: { glob: "trade.rejected.*" } }, { payload.desk: rates }, { payload.notional: { gt: 10000000 } } ] }
  route: [ { to: { team: rates }, channels: [email] } ]
  present: { title: "Rejected {{payload.currency}} {{payload.notional}} — {{payload.reason}}" }
  dedup: { key: [payload.tradeId], mode: until_resolved }

- id: anna-payments-critical
  owner: { user: anna }
  when: { all: [ { $service: payments-api }, { $severity: critical } ] }
  route: [ { to: { user: anna }, channels: { schedule: [ { between: "08:00-20:00", use: [sms] } ], otherwise: [email] } } ]
  runbooks: [high-error-rate-payments]
"""
)

WORKFLOW_EVENT = {
    "type": "WORKFLOW_FAILED",
    "workflowId": "WF-20261023-00042",
    "workflowName": "order-settlement",
    "step": "validate-counterparty",
    "severity": "ERROR",
    "environment": "prod",
    "timestamp": "2026-10-23T09:14:02.331Z",
    "error": {"code": "TIMEOUT", "message": "Counterparty service did not respond in 5000 ms"},
}

ALERTMANAGER_ALERT = {
    "status": "firing",
    "alertname": "HighErrorRate",
    "labels": {
        "service": "payments-api",
        "severity": "critical",
        "instance": "payments-api-2",
        "environment": "prod",
        "region": "eu-west-1",
    },
    "annotations": {"summary": "5xx rate above 5% for 5 minutes"},
    "startsAt": "2026-10-23T09:14:00Z",
}

BUSINESS_EVENT = {
    "meta": {"schema": "trade.rejected.v2", "producer": "trade-gateway", "ts": 1761210842331},
    "payload": {
        "tradeId": "T-998877",
        "desk": "rates",
        "notional": 25000000,
        "currency": "EUR",
        "reason": "LIMIT_BREACH",
    },
}

PRICING_LINE = (
    'ts=2026-10-23T09:14:02.331Z level=WARN service=pricing-engine-2 instrument=EURUSD msg="quote latency high"'
)

RECEIVED = datetime(2026, 10, 23, 9, 14, 3, tzinfo=UTC)
