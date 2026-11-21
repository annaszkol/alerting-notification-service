"""Every route the API serves under /api is in contracts/openapi.yaml (no undocumented endpoints)."""

from services.api.app import create_app
from services.api.schemas import openapi
from services.api.settings import Settings

METHODS = {"get", "put", "post", "patch", "delete"}


def test_api_routes_are_in_the_contract():
    app = create_app(Settings(database_url="postgresql://unused", jwt_secret="x" * 32))
    contract = {(path, m) for path, item in openapi()["paths"].items() for m in item if m in METHODS}
    served = {
        (path.removeprefix("/api"), m)
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/api/")
        for m in item
        if m in METHODS
    }
    assert served, "no routes"
    assert served - contract == set()
