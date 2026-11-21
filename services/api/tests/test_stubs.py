"""Stand-ins for libs/conditions used by the API until A's library is merged (spec §3, §4)."""

import pytest

from services.api.stubs import MISSING, get_path, parse_path, render_live, validate_condition

LETTER = {
    "$service": "payments-api",
    "payload": {"alerts": [{"labels": {"app.kubernetes.io/name": "x"}}], "error": {"code": "TIMEOUT"}},
}


@pytest.mark.parametrize(
    "path, segments",
    [
        ("error.code", ["error", "code"]),
        ("alerts.0.labels", ["alerts", 0, "labels"]),
        ('labels["app.kubernetes.io/name"]', ["labels", "app.kubernetes.io/name"]),
        ('alerts.0.labels["a.b"].c', ["alerts", 0, "labels", "a.b", "c"]),
        ("$service", ["$service"]),
    ],
)
def test_parse_path(path, segments):
    assert parse_path(path) == segments


@pytest.mark.parametrize(
    "path, value",
    [
        ("error.code", "TIMEOUT"),
        ('alerts.0.labels["app.kubernetes.io/name"]', "x"),
        ("$service", "payments-api"),
        ("alerts.5", MISSING),
        ("error.code.x", MISSING),
        ("nope", MISSING),
        ("", MISSING),
    ],
)
def test_get_path(path, value):
    assert get_path(LETTER, path) is value or get_path(LETTER, path) == value


def test_render_live_leaves_other_placeholders_alone():
    assert render_live("{{ $count }} x {{$other}}", {"$count": 3}) == "3 x {{$other}}"
    assert render_live(None, {}) is None


def test_validate_condition_never_raises_on_garbage():
    for garbage in [None, 1, "x", [], {"all": "x"}, {"not": None}, {"a": {"in": 1, "exists": "yes"}}]:
        assert validate_condition(garbage)
