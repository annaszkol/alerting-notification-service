"""Demo event generator (spec §2 "generatory eventów", §11 video script). Uses the spec's sample events.

    python -m mocks.generator workflow            # Settlements failure -> phone + SMS to on-call (test 1)
    python -m mocks.generator trade [--notional N] # trade.rejected.v2 -> email to Rates if N > 10 000 000 (test 3)
    python -m mocks.generator high-error-rate [--resolved]   # payments-api critical -> Anna (tests 4, 5)
    python -m mocks.generator burst --count 500    # 500 identical workflow failures -> one alert, count 500 (test 8)
    python -m mocks.generator pricing --lines 2 [--file PATH]   # WARN lines from two pricing engines (test 2)
    python -m mocks.generator unknown              # an event no mapping recognises (test 11)

Env: INGEST_URL (http://localhost:8080), SEED_INGEST_TOKEN (dev-ingest-token).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from libs.conditions import spec_examples as ex

INGEST = os.environ.get("INGEST_URL", "http://localhost:8080")
TOKEN = os.environ.get("SEED_INGEST_TOKEN", "dev-ingest-token")
PRICING_LOG = os.environ.get("PRICING_LOG", "/var/log/pricing/pricing-engine.log")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def post(source: str, payload: Any, key: str | None = None) -> int:
    headers = {"Content-Type": "application/json", "X-Source-Token": TOKEN}
    if key:
        headers["Idempotency-Key"] = key
    request = urllib.request.Request(f"{INGEST}/ingest/{source}", json.dumps(payload).encode(), headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status


def workflow(workflow_id: str | None = None) -> dict[str, Any]:
    return {**ex.WORKFLOW_EVENT, "workflowId": workflow_id or f"WF-{random.randint(10**5, 10**6)}", "timestamp": now()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the spec's sample events")
    sub = parser.add_subparsers(dest="what", required=True)
    sub.add_parser("workflow")
    trade = sub.add_parser("trade")
    trade.add_argument("--notional", type=int, default=25_000_000)
    rate = sub.add_parser("high-error-rate")
    rate.add_argument("--resolved", action="store_true")
    burst = sub.add_parser("burst")
    burst.add_argument("--count", type=int, default=500)
    burst.add_argument("--workers", type=int, default=16)
    pricing = sub.add_parser("pricing")
    pricing.add_argument("--lines", type=int, default=2)
    pricing.add_argument("--file", default=PRICING_LOG)
    sub.add_parser("unknown")
    args = parser.parse_args(argv)

    if args.what == "workflow":
        print(post("workflows", workflow()))
    elif args.what == "trade":
        tid = f"T-{random.randint(10**5, 10**6)}"
        meta = {**ex.BUSINESS_EVENT["meta"], "ts": int(time.time() * 1000)}
        trade_payload = {**ex.BUSINESS_EVENT["payload"], "tradeId": tid, "notional": args.notional}
        print(post("trades", {"meta": meta, "payload": trade_payload}))
    elif args.what == "high-error-rate":
        status = "resolved" if args.resolved else "firing"
        print(post("monitoring", {**ex.ALERTMANAGER_ALERT, "status": status, "startsAt": now()}))
    elif args.what == "burst":
        event = workflow(f"WF-BURST-{random.randint(10**5, 10**6)}")  # identical payload, distinct event ids
        started = time.monotonic()
        with ThreadPoolExecutor(args.workers) as pool:
            codes = list(pool.map(lambda _: post("workflows", event), range(args.count)))
        print(f"{codes.count(202)}/{args.count} accepted in {time.monotonic() - started:.1f}s ({event['workflowId']})")
    elif args.what == "pricing":
        os.makedirs(os.path.dirname(args.file), exist_ok=True)
        with open(args.file, "a", encoding="utf-8") as f:
            for i in range(args.lines):
                engine = f"pricing-engine-{i % 2 + 1}"
                f.write(f'ts={now()} level=WARN service={engine} instrument=EURUSD msg="quote latency high"\n')
        print(f"{args.lines} lines -> {args.file}")
    elif args.what == "unknown":
        print(post("http-default", {"vendor": "acme", "check": {"name": "disk", "usage.pct": 97}, "ts": now()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
