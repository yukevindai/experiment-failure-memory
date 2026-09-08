from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import add, experiment
from fastapi.testclient import TestClient

from failure_memory.app import create_app


def test_revision_concurrency_and_persistence(setup):
    app, c, lab, p, _ = setup
    a = c["alice"]
    r = add(a, p)

    def update(title):
        return a.put(
            f"/api/records/{r['id']}",
            json={"expected_version": 1, "record": experiment(title=title)},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, ["First edit", "Second edit"])) == [200, 409]
    detail = a.get(f"/api/records/{r['id']}").json()
    assert len(detail["history"]) == 2
    assert detail["history"][0]["record"]["title"] == "Electrolyte precipitation"
    assert detail["version"] == 2
    fresh = create_app(app.state.database.path, origin="https://testserver")
    with TestClient(
        fresh, base_url="https://testserver", cookies=a.cookies
    ) as restored:
        assert restored.get(f"/api/records/{r['id']}").json()["version"] == 2


def test_relationship_cycles_and_cross_project(setup):
    _, c, _, p, secret = setup
    a = c["alice"]
    r1, r2, r3 = [add(a, p) for _ in range(3)]
    for s, t in ((r2, r1), (r3, r2)):
        assert (
            a.post(
                f"/api/records/{s['id']}/links",
                json={"target_id": t["id"], "relation": "repeat_of"},
            ).status_code
            == 201
        )
    assert (
        a.post(
            f"/api/records/{r1['id']}/links",
            json={"target_id": r3["id"], "relation": "repeat_of"},
        ).status_code
        == 409
    )
    cross = add(a, secret)
    assert (
        a.post(
            f"/api/records/{r1['id']}/links",
            json={"target_id": cross["id"], "relation": "fix_for"},
        ).status_code
        == 400
    )


def test_similarity_converts_units_and_reports_evidence(setup):
    _, c, _, p, _ = setup
    a = c["alice"]
    one = add(a, p)
    two = add(
        a,
        p,
        experiment(conditions=[{"name": "temperature", "value": 298.15, "unit": "K"}]),
    )
    result = a.get(f"/api/records/{one['id']}/similar").json()
    assert result[0]["id"] == two["id"]
    assert result[0]["condition_comparisons"][0]["relative_distance"] == pytest.approx(
        0
    )
    assert result[0]["matched_terms"]
    patterns = a.get(f"/api/projects/{p}/patterns").json()
    assert patterns["causes"][0]["record_count"] == 2
    assert patterns["recovery_strategies"][0]["reported_outcomes"] == {"untried": 2}
    assert "not causal" in patterns["interpretation"]


def test_attachment_integrity_and_audit(setup):
    app, c, _, p, _ = setup
    a = c["alice"]
    r = add(a, p)
    attachment = a.post(
        f"/api/records/{r['id']}/attachments?filename=../../secret.html",
        content=b"<script>alert(1)</script>",
    ).json()
    assert attachment["filename"] == "secret.html"
    response = a.get("/api/attachments/" + attachment["id"])
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    with app.state.database.transaction() as db:
        db.execute(
            "UPDATE attachments SET content=? WHERE id=?",
            (b"tampered", attachment["id"]),
        )
    assert a.get("/api/attachments/" + attachment["id"]).status_code == 409
    assert a.post(f"/api/projects/{p}/export?attachments=true").status_code == 409
    assert any(
        e["action"] == "attachment.downloaded"
        for e in a.get(f"/api/projects/{p}/events").json()
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("performed_at", "2026-01-01T00:00:00"),
        ("uncertainty_notes", ""),
        ("conditions", [{"name": "x", "value": True, "unit": "K"}]),
        ("conditions", [{"name": "x", "value": "12", "unit": "K"}]),
        ("conditions", [{"name": "x", "value": 1, "unit": "notAUnit"}]),
        ("measurements", [{"name": "x", "value": 1, "unit": "K", "uncertainty": -1}]),
        (
            "conditions",
            [
                {"name": "x", "value": 1, "unit": "K"},
                {"name": "X", "value": 2, "unit": "K"},
            ],
        ),
    ],
)
def test_invalid_scientific_records_rejected(setup, field, value):
    _, c, _, p, _ = setup
    assert (
        c["alice"]
        .post(f"/api/projects/{p}/records", json=experiment(**{field: value}))
        .status_code
        == 422
    )


def test_body_limits_and_static_content(setup):
    _, c, lab, _, _ = setup
    a = c["alice"]
    assert (
        a.post(
            "/api/labs",
            content=b"x" * (1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )
    assert (
        a.post("/api/labs", content=iter([b"x" * 600000, b"x" * 600000])).status_code
        == 413
    )
    assert (
        a.put(
            f"/api/labs/{lab}/members", json={"username": "??", "role": "member"}
        ).status_code
        == 422
    )
    response = a.get("/")
    assert response.status_code == 200 and "Experiment Failure Memory" in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert a.get("/static/app.js").status_code == 200
    assert a.get("/static/../../schema.sql").status_code == 404


def test_idempotent_eln_import_and_comments(setup):
    _, c, _, p, _ = setup
    a = c["alice"]
    payload = {
        "schema_version": "1.0",
        "connector": "generic_eln",
        "external_id": "external-1",
        "record": experiment(),
    }
    one = a.post(f"/api/projects/{p}/import", json=payload).json()
    two = a.post(f"/api/projects/{p}/import", json=payload).json()
    assert two["replayed"] and one["record"]["id"] == two["record"]["id"]
    payload["record"]["title"] = "Changed external record"
    assert a.post(f"/api/projects/{p}/import", json=payload).status_code == 409
    r = one["record"]
    a.post(f"/api/records/{r['id']}/comments", json={"body": "Follow-up observation"})
    detail = a.get(f"/api/records/{r['id']}").json()
    assert detail["comments"][0]["body"] == "Follow-up observation"
    a.post(
        f"/api/records/{r['id']}/archive",
        json={"expected_version": 1, "archived": True},
    )
    assert a.get("/api/search").json() == []
    assert len(a.get("/api/search?archived=true").json()) == 1


@pytest.mark.parametrize(
    "number", ["NaN", "Infinity", "-Infinity", "1e999", str(10**400)]
)
def test_nonfinite_or_overflow_json_is_safe_validation_error(setup, number):
    import json

    _, c, _, p, _ = setup
    raw = json.dumps(
        experiment(conditions=[{"name": "x", "value": "REPLACE", "unit": "K"}])
    ).replace('"REPLACE"', number)
    response = c["alice"].post(
        f"/api/projects/{p}/records",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert "input" not in response.json()["detail"][0]
