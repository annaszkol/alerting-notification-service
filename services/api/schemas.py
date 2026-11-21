"""Request validation straight from `contracts/` so the API and the contract cannot drift apart."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
BASE = "https://alerting.local/contracts/"
JSON_SCHEMAS = ("route.schema.json", "runbook.schema.json", "ws-messages.schema.json", "notify.schema.json")


@cache
def openapi() -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))


@cache
def registry() -> Registry:
    resources = [
        (BASE + "openapi.yaml", Resource.from_contents(openapi(), default_specification=DRAFT202012))
    ]
    for name in JSON_SCHEMAS:
        doc = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
        resources.append((BASE + name, Resource.from_contents(doc)))
    return Registry().with_resources(resources)


@cache
def validator(name: str) -> Draft202012Validator:
    """Validator for `components.schemas.<name>` of openapi.yaml."""
    return Draft202012Validator(
        {"$ref": f"{BASE}openapi.yaml#/components/schemas/{name}"}, registry=registry()
    )


def errors(name: str, document: Any) -> list[dict[str, str]]:
    """FieldError list (empty = valid), sorted by path."""
    out = []
    for err in validator(name).iter_errors(document):
        path = ".".join(str(p) for p in err.absolute_path) or "body"
        out.append({"path": path, "message": err.message})
    return sorted(out, key=lambda e: e["path"])
