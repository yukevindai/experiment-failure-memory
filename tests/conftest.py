import pytest
from fastapi.testclient import TestClient

from failure_memory.app import create_app
from failure_memory.auth import create_user

PASSWORD = "synthetic-test-password-123"


def experiment(**changes):
    return (
        dict(
            title="Electrolyte precipitation",
            summary="Synthetic test record",
            performed_at="2026-01-01T12:00:00Z",
            status="failed",
            materials=[{"name": "Synthetic salt", "lot": "TEST-01"}],
            conditions=[{"name": "temperature", "value": 25, "unit": "degC"}],
            procedure=["Mix the synthetic example"],
            equipment=[{"name": "Test balance", "asset_id": "DEMO"}],
            outcomes="Visible precipitate in this synthetic example",
            measurements=[
                {
                    "name": "conductivity",
                    "value": 1.2,
                    "unit": "mS/cm",
                    "uncertainty": 0.1,
                }
            ],
            suspected_causes=[
                {
                    "cause": "Water contamination",
                    "confidence": "low",
                    "rationale": "Not measured directly",
                }
            ],
            fixes=[
                {
                    "action": "Dry materials",
                    "outcome": "untried",
                    "evidence": "Planned, not yet attempted",
                }
            ],
            uncertainty_notes="Synthetic observations only",
            tags=["synthetic"],
            source={"kind": "notebook", "reference": "SYNTHETIC page 1"},
            **changes,
        )
        if not changes
        else {**experiment(), **changes}
    )


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path / "memory.sqlite", origin="https://testserver")
    db = app.state.database
    for name in ("alice", "bob", "carol"):
        create_user(db, name, name.title(), PASSWORD)
    clients = {}
    for name in ("alice", "bob", "carol"):
        client = TestClient(app, base_url="https://testserver")
        client.headers["X-EFM-Request"] = "1"
        response = client.post(
            "/api/login", json={"username": name, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        clients[name] = client
    a = clients["alice"]
    lab = a.post("/api/labs", json={"name": "Synthetic lab"}).json()["id"]
    project = a.post(f"/api/labs/{lab}/projects", json={"name": "Electrolytes"}).json()[
        "id"
    ]
    secret = a.post(
        f"/api/labs/{lab}/projects", json={"name": "Private project"}
    ).json()["id"]
    yield app, clients, lab, project, secret
    for client in clients.values():
        client.close()


def add(client, project, payload=None):
    response = client.post(
        f"/api/projects/{project}/records", json=payload or experiment()
    )
    assert response.status_code == 201, response.text
    return response.json()


def grant(a, lab, project, user="bob", role="viewer"):
    assert (
        a.put(
            f"/api/labs/{lab}/members", json={"username": user, "role": "member"}
        ).status_code
        == 200
    )
    assert (
        a.put(
            f"/api/projects/{project}/members", json={"username": user, "role": role}
        ).status_code
        == 200
    )
