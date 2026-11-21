"""Load generator helpers: schedule of unique/duplicate events, percentiles, matching SMS to events."""

from loadtest import run


def test_schedule_repeats_each_unique_event_dup_times():
    args = run.cli(["--name", "x", "--rate", "10", "--duration", "2", "--dup", "4"])
    ids = run.schedule(args, "abc")
    assert len(ids) == 20 and len(set(ids)) == 5 and ids[:4] == ["LT-abc-0"] * 4
    assert len(run.schedule(run.cli(["--name", "x", "--unique", "7"]), "abc")) == 7


def test_percentile_interpolates_raw_samples():
    values = [float(v) for v in range(1, 101)]
    assert run.percentile(values, 50) == 50.5
    assert run.percentile(values, 99) == 99.01
    assert run.percentile([], 50) is None


def test_first_sms_per_workflow_of_this_run(monkeypatch):
    messages = [
        {"text": "[ERROR] Settlement failed: LT-abc-1 http://x/a/1 ACK 12", "receivedAt": "2026-11-21T10:00:02.000Z"},
        {"text": "[ERROR] Settlement failed: LT-abc-1 http://x/a/1 ACK 13", "receivedAt": "2026-11-21T10:00:01.000Z"},
        {"text": "[ERROR] Settlement failed: LT-other-1 http://x/a/2", "receivedAt": "2026-11-21T10:00:01.000Z"},
        {"text": "[ERROR] Settlement failed: WF-SMOKE-1 http://x/a/3", "receivedAt": "2026-11-21T10:00:01.000Z"},
    ]

    class Response:
        def json(self):
            return messages

    monkeypatch.setattr(run.httpx, "get", lambda *a, **k: Response())
    assert run.first_sms("abc") == {"LT-abc-1": run.parse("2026-11-21T10:00:01.000Z")}


def test_payload_carries_sent_at():
    p = run.payload("LT-abc-1", 1_700_000_000.123)
    assert p["sentAt"] == "2023-11-14T22:13:20.123Z" and p["workflowId"] == "LT-abc-1"
