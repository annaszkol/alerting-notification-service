"""Load generator and latency probe (spec §10 "Pomiar").

Sends WORKFLOW_FAILED events for the seeded rule `settlements-prod-failures` (dedup by workflowId, SMS to on-call).
Every unique event gets workflowId `LT-<run>-<seq>` and carries `sentAt`. End of the measurement is the fake SMS
gateway: latency = receivedAt of the first SMS for that workflowId - sentAt (generator and gateway share the
host clock). Percentiles come from the raw samples written to `<out>/samples.csv`.

    python loadtest/run.py --name unique --rate 100 --duration 60
    python loadtest/run.py --name burst --rate 500 --duration 10 --dup 20
    python loadtest/run.py --name outage --rate 50 --duration 60 --at 15 "curl ..." --at 35 "curl ..."
    python loadtest/run.py --name backlog --via kafka --unique 20000 --no-wait   # only produce

Modes: `--via http` (ingest, waits for the Kafka ack like a real sender) or `--via kafka` (envelopes straight into
events.raw, to load the processor beyond what one HTTP client can push).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

INGEST = os.environ.get("INGEST_URL", "http://localhost:8080")
GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8090")
TOKEN = os.environ.get("SEED_INGEST_TOKEN", "dev-ingest-token")
BROKERS = os.environ.get("KAFKA_BROKERS", "localhost:19092")
SMS_ID = re.compile(r"Settlement failed: (LT-[0-9a-f]+-\d+)")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def payload(workflow_id: str, sent_at: float) -> dict:
    return {
        "type": "WORKFLOW_FAILED", "workflowId": workflow_id, "workflowName": "order-settlement",
        "step": "validate-counterparty", "severity": "ERROR", "environment": "prod",
        "timestamp": iso(sent_at), "sentAt": iso(sent_at),
        "error": {"code": "TIMEOUT", "message": "load test"},
    }  # fmt: skip


def schedule(args: argparse.Namespace, run: str) -> list[str]:
    """workflowId of every event in send order; each unique id is repeated --dup times (burst of duplicates)."""
    unique = args.unique or int(args.rate * args.duration / args.dup)
    return [f"LT-{run}-{i}" for i in range(unique) for _ in range(args.dup)]


async def send_http(ids: list[str], rate: float, concurrency: int) -> dict[int, float | None]:
    """Paced sends; returns index -> sentAt (None when ingest refused the event)."""
    sent: dict[int, float | None] = {}
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(len(ids)):
        queue.put_nowait(i)
    start = time.time()
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits, timeout=30) as client:

        async def worker() -> None:
            while not queue.empty():
                i = queue.get_nowait()
                delay = start + i / rate - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                at = time.time()
                try:
                    r = await client.post(
                        f"{INGEST}/ingest/workflows", json=payload(ids[i], at),
                        headers={"X-Source-Token": TOKEN, "Idempotency-Key": f"{ids[i]}-{i}"},
                    )  # fmt: skip
                    sent[i] = at if r.status_code == 202 else None
                except httpx.HTTPError:
                    sent[i] = None

        await asyncio.gather(*(worker() for _ in range(concurrency)))
    return sent


def send_kafka(ids: list[str], rate: float) -> dict[int, float | None]:
    from kafka import KafkaProducer

    producer = KafkaProducer(bootstrap_servers=BROKERS.split(","), acks="all", linger_ms=5)
    start, sent = time.time(), {}
    for i, workflow_id in enumerate(ids):
        if rate:
            delay = start + i / rate - time.time()
            if delay > 0:
                time.sleep(delay)
        at = time.time()
        envelope = {
            "eventId": f"lt:{workflow_id}:{i}", "sourceId": "workflows", "receivedAt": iso(at),
            "contentType": "application/json", "payload": payload(workflow_id, at),
        }  # fmt: skip
        producer.send("events.raw", key=envelope["eventId"].encode(), value=json.dumps(envelope).encode())
        sent[i] = at
    producer.flush()
    return sent


def first_sms(run: str) -> dict[str, float]:
    messages = httpx.get(f"{GATEWAY}/api/messages", params={"channel": "sms", "direction": "out"}, timeout=60).json()
    if isinstance(messages, dict):
        messages = messages.get("messages", [])
    first: dict[str, float] = {}
    for m in messages:
        match = SMS_ID.search(m.get("text") or "")
        if match and match.group(1).startswith(f"LT-{run}-"):
            at = parse(m["receivedAt"])
            first[match.group(1)] = min(at, first.get(match.group(1), at))
    return first


def percentile(sorted_values: list[float], p: float) -> float | None:
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


async def run_at(start: float, at: list[tuple[str, str]]) -> None:
    """Fault injection: run shell commands at given seconds after the start."""
    for seconds, command in sorted(at, key=lambda a: float(a[0])):
        await asyncio.sleep(max(0, start + float(seconds) - time.time()))
        print(f"[{time.time() - start:6.1f}s] {command}", flush=True)
        await asyncio.to_thread(subprocess.run, command, shell=True, check=False)


async def main(args: argparse.Namespace) -> int:
    run = uuid.uuid4().hex[:8]
    ids = schedule(args, run)
    out = Path(args.out) / args.name
    out.mkdir(parents=True, exist_ok=True)
    print(f"run {run}: {len(ids)} events ({len(set(ids))} unique) via {args.via} at {args.rate}/s", flush=True)

    start = time.time()
    faults = asyncio.create_task(run_at(start, args.at or []))
    if args.via == "http":
        sent = await send_http(ids, args.rate, args.concurrency)
    else:
        sent = await asyncio.to_thread(send_kafka, ids, args.rate)
    send_seconds = time.time() - start
    accepted = {i: t for i, t in sent.items() if t is not None}
    first_sent: dict[str, float] = {}
    for i, at in accepted.items():
        first_sent[ids[i]] = min(at, first_sent.get(ids[i], at))
    print(f"sent {len(accepted)}/{len(ids)} in {send_seconds:.1f}s ({len(accepted) / send_seconds:.0f}/s)", flush=True)

    received: dict[str, float] = {}
    if not args.no_wait:
        deadline = time.time() + args.drain
        while time.time() < deadline:
            received = first_sms(run)
            if len(received) >= len(first_sent):
                break
            await asyncio.sleep(2)
    await faults

    latencies = sorted((received[w] - first_sent[w]) * 1000 for w in received if w in first_sent)
    with open(out / "samples.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["workflowId", "sentAt", "smsReceivedAt", "latencyMs"])
        for w, at in sorted(first_sent.items(), key=lambda x: x[1]):
            got = received.get(w)
            writer.writerow([w, iso(at), iso(got) if got else "", round((got - at) * 1000) if got else ""])
    summary = {
        "name": args.name, "run": run, "via": args.via, "targetRate": args.rate, "dup": args.dup,
        "events": len(ids), "accepted": len(accepted), "rejected": len(ids) - len(accepted),
        "sendSeconds": round(send_seconds, 2), "achievedRate": round(len(accepted) / send_seconds, 1),
        "expectedAlerts": len(first_sent), "deliveredAlerts": len(latencies),
        "latencyMs": {f"p{p}": round(v) for p in (50, 95, 99) if (v := percentile(latencies, p)) is not None}
        | ({"max": round(latencies[-1])} if latencies else {}),
        "faults": args.at or [],
    }  # fmt: skip
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    return 0 if args.no_wait or summary["deliveredAlerts"] == summary["expectedAlerts"] else 1


def cli(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True, help="scenario name, results go to <out>/<name>/")
    p.add_argument("--via", choices=["http", "kafka"], default="http")
    p.add_argument("--rate", type=float, default=100, help="events per second (0 with kafka: as fast as possible)")
    p.add_argument("--duration", type=float, default=60)
    p.add_argument("--unique", type=int, default=0, help="number of unique events (overrides rate * duration)")
    p.add_argument("--dup", type=int, default=1, help="copies of every unique event (duplicate burst)")
    p.add_argument("--concurrency", type=int, default=64)
    p.add_argument("--drain", type=float, default=180, help="seconds to wait for the SMS after sending")
    p.add_argument("--no-wait", action="store_true", help="only send, do not wait for SMS")
    p.add_argument("--at", nargs=2, action="append", metavar=("SECONDS", "COMMAND"), help="run a command mid-test")
    p.add_argument("--out", default=str(Path(__file__).parent / "results"))
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(cli())))
