"""Smoke tests for the skeleton: every service module imports and HTTP services answer /healthz."""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("service", ["api", "ingest"])
def test_http_service_healthz(service):
    app = importlib.import_module(f"services.{service}.app").app
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": service}


@pytest.mark.parametrize("service", ["processor", "delivery", "executor"])
def test_worker_module_imports(service):
    module = importlib.import_module(f"services.{service}.__main__")
    assert callable(module.main)
