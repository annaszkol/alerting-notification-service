"""POST /runbooks/import (contracts/openapi.yaml `importRunbook`, spec §7)."""

from services.api.tests.helpers import assert_matches

IMPORT_YAML = "steps:\n  - action: restart_service\n    service: pricing-engine-1\n  - text: Watch latency\n"


def test_import_preview_save_and_approval(client, login, conn):
    conn.execute("DELETE FROM runbooks WHERE id IN ('imported', 'broken')")
    body = {"id": "imported", "owner": {"team": "rates"}, "format": "yaml", "original": IMPORT_YAML}
    assert client.post("/api/runbooks/import", json=body, headers=login("mia")).status_code == 403  # member

    preview = client.post("/api/runbooks/import?dryRun=true", json=body, headers=login("marek"))
    assert preview.status_code == 200
    assert_matches("Runbook", preview.json())
    assert [s["id"] for s in preview.json()["canonical"]["steps"]] == ["restart", "step"]
    assert client.get("/api/runbooks/imported", headers=login("marek")).status_code == 404  # nothing saved

    saved = client.post("/api/runbooks/import", json=body, headers=login("marek"))
    assert saved.status_code == 201
    assert_matches("Runbook", saved.json())
    assert (saved.json()["approvedBy"], saved.json()["updatedBy"]) == (None, "marek")  # not approved yet
    assert client.post("/api/runbooks/import", json=body, headers=login("marek")).status_code == 409

    bad = {**body, "id": "broken", "original": "steps: [unclosed"}
    response = client.post("/api/runbooks/import", json=bad, headers=login("marek"))
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["path"] == "original"

    link = {"id": "broken", "owner": {"team": "rates"}, "format": "link", "original": "https://wiki.example.test/x"}
    assert client.post("/api/runbooks/import", json=link, headers=login("marek")).json()["canonical"]["link"]
