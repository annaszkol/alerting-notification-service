"""libs/conditions against the spec's own examples (§3 mappings, §4 rules, "Sprawdzenie na sample events")."""

import pytest

from libs.conditions import (
    MISSING,
    Envelope,
    SchemaMapping,
    Source,
    evaluate,
    get_path,
    map_event,
    parse_path,
    render,
    render_live,
    shape_hash,
    validate_condition,
)
from libs.conditions import spec_examples as ex

MAPPINGS = [SchemaMapping.from_doc(m) for m in ex.MAPPINGS]
RULES = {r["id"]: r for r in ex.RULES}
HTTP = Source(id="http-default", type="http")
LOGS = Source(id="pricing-logs", type="logfile", parser="logfmt")


def letters(payload, source=HTTP, event_id="evt-1"):
    return map_event(Envelope(event_id, source.id, ex.RECEIVED, "application/json", payload), source, MAPPINGS)


def matching(letter):
    return sorted(rid for rid, rule in RULES.items() if evaluate(rule["when"], letter))


def test_paths():
    assert parse_path('labels["app.kubernetes.io/name"]') == ["labels", "app.kubernetes.io/name"]
    assert parse_path("alerts.0.labels") == parse_path("alerts[0].labels") == ["alerts", 0, "labels"]
    letter = {"$service": "x", "payload": {"a": [{"b": 1}], "k.v": 2}}
    assert get_path(letter, "a.0.b") == 1
    assert get_path(letter, '["k.v"]') == 2
    assert get_path(letter, "$service") == "x"
    assert get_path(letter, "a.5.b") is MISSING
    assert get_path(letter, "a.0.b.c") is MISSING


def test_spec_sample_events_match_the_spec_rules():
    [workflow] = letters(ex.WORKFLOW_EVENT)
    [business] = letters(ex.BUSINESS_EVENT)
    [webhook] = letters(ex.ALERTMANAGER_ALERT)
    [log] = map_event(Envelope("evt-4", "pricing-logs", ex.RECEIVED, "text/logfmt", ex.PRICING_LINE), LOGS, MAPPINGS)
    assert matching(workflow.letter) == ["settlements-prod-failures"]
    assert matching(business.letter) == ["rates-large-rejections"]
    assert matching(webhook.letter) == ["anna-payments-critical"]
    assert matching(log.letter) == ["pricing-warnings"]
    title = render(RULES["rates-large-rejections"]["present"]["title"], business.letter)
    assert title == "Rejected EUR 25000000 — LIMIT_BREACH"
    small = {**ex.BUSINESS_EVENT, "payload": {**ex.BUSINESS_EVENT["payload"], "notional": 5000000}}
    assert matching(letters(small)[0].letter) == []  # spec §13 test 3


def test_mapping_fields():
    w = letters(ex.WORKFLOW_EVENT)[0].letter
    assert (w["$schema"], w["$service"], w["$severity"], w["$severityRaw"]) == (
        "workflow",
        "order-settlement",
        "error",
        "ERROR",
    )
    assert w["$summary"] == "order-settlement / validate-counterparty: Counterparty service did not respond in 5000 ms"
    assert (w["$correlationKey"], w["$status"], w["$eventTime"]) == (
        "WF-20261023-00042",
        "firing",
        "2026-10-23T09:14:02.331000+00:00",
    )
    b = letters(ex.BUSINESS_EVENT)[0].letter
    assert (b["$schema"], b["$service"], b["$eventTime"][:19]) == (
        "trade.rejected.v2",
        "trade-gateway",
        "2025-10-23T09:14:02",
    )
    assert b["$severity"] == "info"  # source default (business events have no severity)


def test_split_and_single_alertmanager_shapes():
    batch = letters({"alerts": [ex.ALERTMANAGER_ALERT, {**ex.ALERTMANAGER_ALERT, "status": "resolved"}]})
    assert [m.letter["$status"] for m in batch] == ["firing", "resolved"]
    assert [m.letter["$eventId"] for m in batch] == ["evt-1#0", "evt-1#1"]
    assert letters(ex.ALERTMANAGER_ALERT)[0].letter["$correlationKey"] == "HighErrorRate|payments-api|eu-west-1"


def test_unmapped_and_sources_restriction():
    [m] = letters({"foo": 1})
    assert (m.mapping, m.letter["$schema"]) == (None, "unmapped")
    [log_over_http] = letters({"level": "WARN"})  # pricing-log is limited to the pricing-logs source
    assert log_over_http.mapping is None
    assert shape_hash({"a": 1, "b": {"c": 2}}) == shape_hash({"b": {"c": 9}, "a": 0}) != shape_hash({"a": 1})


@pytest.mark.parametrize(
    ("cond", "expected"),
    [
        ({"n": {"gt": 10}}, True),  # "25" compares as a number
        ({"n": 25}, True),
        ({"missing": {"exists": False}}, True),
        ({"not": {"missing": "x"}}, True),
        ({"missing": {"ne": "x"}}, False),  # missing field -> false
        ({"$severity": {"gte": "warning"}}, True),
        ({"$severity": {"gt": "error"}}, False),
        ({"tags": {"contains": "b"}}, True),
        ({"name": {"regex": "^pri.*-2$"}}, True),
        ({"name": {"regex": "("}}, False),  # invalid regex never throws
        ({"name": {"in": ["x", "pricing-engine-2"]}}, True),
        ({"any": []}, False),
        ({"field": "name", "op": "glob", "value": "pricing-*"}, True),  # contracts/condition.py form
        ("garbage", False),
    ],
)
def test_operators(cond, expected):
    letter = {"$severity": "error", "payload": {"n": "25", "tags": ["a", "b"], "name": "pricing-engine-2"}}
    assert evaluate(cond, letter) is expected


def test_templates_and_validation():
    letter = {"$service": "s", "payload": {"a": None}}
    assert render("{{a}} {{nope}} {{$service}} {{$count}}", letter) == "— — s {{$count}}"
    assert render_live("{{$count}}x {{$state}}", {"$count": 5, "$state": "open"}) == "5x open"
    assert render("{{__class__.__init__}}", letter) == "—"  # no code, no attribute access
    assert validate_condition({"all": [{"x": {"bogus": 1}}]}) == [
        {"path": "when.all.0.x.bogus", "message": "unknown operator `bogus`"}
    ]


def test_explain_mirrors_evaluate():
    from libs.conditions import explain

    rule = RULES["rates-large-rejections"]["when"]
    small = {**ex.BUSINESS_EVENT, "payload": {**ex.BUSINESS_EVENT["payload"], "notional": 5000000}}
    letter = letters(small)[0].letter
    tree = explain(rule, letter)
    assert tree["result"] is evaluate(rule, letter) is False
    schema, desk, notional = tree["children"]
    assert (schema["op"], schema["actual"], schema["result"]) == ("glob", "trade.rejected.v2", True)
    assert (desk["op"], desk["expected"], desk["result"]) == ("eq", "rates", True)
    assert (notional["op"], notional["actual"], notional["expected"], notional["result"]) == (
        "gt",
        5000000,
        10000000,
        False,
    )
    missing = explain({"nope": {"exists": True}}, letter)
    assert (missing["missing"], missing["result"], "actual" in missing) == (True, False, False)
    assert explain("garbage", letter)["result"] is False
