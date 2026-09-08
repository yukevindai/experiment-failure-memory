from conftest import PASSWORD, add, experiment, grant
from fastapi.testclient import TestClient

from failure_memory.auth import reset_password


def test_project_isolation_and_live_revocation(setup):
    app, c, lab, p, secret = setup
    a, b, outsider = c["alice"], c["bob"], c["carol"]
    grant(a, lab, p)
    allowed = add(a, p)
    hidden = add(a, secret, experiment(title="SECRETPROJECTTOKEN"))
    attachment = a.post(
        f"/api/records/{hidden['id']}/attachments?filename=secret.txt",
        content=b"private",
    ).json()
    assert len(b.get("/api/projects").json()) == 1
    assert len(b.get("/api/search").json()) == 1
    assert b.get("/api/search?q=SECRETPROJECTTOKEN").json() == []
    for user in (b, outsider):
        for path in [
            f"/records/{hidden['id']}",
            f"/records/{hidden['id']}/similar",
            f"/attachments/{attachment['id']}",
            f"/projects/{secret}/patterns",
            f"/projects/{secret}/events",
            f"/projects/{secret}/members",
            f"/search?project_id={secret}",
        ]:
            assert user.get("/api" + path).status_code == 404, path
        assert user.post(f"/api/projects/{secret}/export").status_code == 404
        assert (
            user.post(
                f"/api/projects/{secret}/import",
                json={
                    "schema_version": "1.0",
                    "connector": "demo",
                    "external_id": "1",
                    "record": experiment(),
                },
            ).status_code
            == 404
        )
    assert b.get(f"/api/records/{allowed['id']}").status_code == 200
    assert b.post(f"/api/projects/{p}/export").status_code == 200
    assert (
        a.put(
            f"/api/projects/{p}/members", json={"username": "bob", "role": "remove"}
        ).status_code
        == 200
    )
    assert b.get(f"/api/records/{allowed['id']}").status_code == 404
    assert b.get("/api/search").json() == []
    assert b.post(f"/api/projects/{p}/export").status_code == 404


def test_viewer_cannot_write_and_lab_removal_revokes_grants(setup):
    app, c, lab, p, _ = setup
    a, b = c["alice"], c["bob"]
    grant(a, lab, p)
    r = add(a, p)
    other = add(a, p)
    writes = [
        ("post", f"/projects/{p}/records", experiment()),
        ("put", f"/records/{r['id']}", {"expected_version": 1, "record": experiment()}),
        ("post", f"/records/{r['id']}/comments", {"body": "x"}),
        (
            "post",
            f"/records/{r['id']}/archive",
            {"expected_version": 1, "archived": True},
        ),
        (
            "post",
            f"/records/{r['id']}/links",
            {"target_id": other["id"], "relation": "repeat_of"},
        ),
        ("put", f"/projects/{p}/members", {"username": "carol", "role": "admin"}),
    ]
    for method, path, payload in writes:
        assert getattr(b, method)("/api" + path, json=payload).status_code == 403, path
    assert (
        b.post(
            f"/api/records/{r['id']}/attachments?filename=x", content=b"x"
        ).status_code
        == 403
    )
    assert b.get(f"/api/projects/{p}/events").status_code == 403
    assert (
        a.put(
            f"/api/labs/{lab}/members", json={"username": "bob", "role": "remove"}
        ).status_code
        == 200
    )
    assert b.get("/api/projects").json() == []
    a.put(f"/api/labs/{lab}/members", json={"username": "bob", "role": "member"})
    assert b.get("/api/projects").json() == []


def test_lab_admin_inheritance_and_owner_protection(setup):
    _, c, lab, p, secret = setup
    a, b = c["alice"], c["bob"]
    a.put(f"/api/labs/{lab}/members", json={"username": "bob", "role": "admin"})
    assert {x["id"] for x in b.get("/api/projects").json()} == {p, secret}
    assert (
        b.put(
            f"/api/labs/{lab}/members", json={"username": "alice", "role": "remove"}
        ).status_code
        == 409
    )
    assert (
        b.put(
            f"/api/labs/{lab}/members", json={"username": "carol", "role": "admin"}
        ).status_code
        == 403
    )
    a.put(f"/api/labs/{lab}/members", json={"username": "bob", "role": "member"})
    assert b.get("/api/projects").json() == []


def test_auth_csrf_cookie_and_password_reset(setup):
    app, c, _, p, _ = setup
    a = c["alice"]
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get("/api/search").status_code == 401
    assert (
        anonymous.post(
            "/api/login", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 403
    )
    assert (
        a.post(
            "/api/labs",
            json={"name": "Bad"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        a.post(
            "/api/labs", json={"name": "Bad"}, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        a.post(
            "/api/labs", json={"name": "Bad"}, headers={"X-EFM-Request": "0"}
        ).status_code
        == 403
    )
    login = anonymous.post(
        "/api/login",
        json={"username": "alice", "password": PASSWORD},
        headers={"X-EFM-Request": "1"},
    )
    cookie = login.headers["set-cookie"].lower()
    for flag in ("httponly", "secure", "samesite=strict"):
        assert flag in cookie
    assert (
        a.post(
            "/api/labs",
            json={"name": "Invalid token"},
            headers={b"X-CSRF-Token": b"\xff"},
        ).status_code
        == 403
    )
    reset_password(app.state.database, "alice", "replacement-password-456")
    assert a.get("/api/me").status_code == 401
    assert anonymous.get("/api/me").status_code == 401
    anonymous.close()


def test_login_rate_limit_commits_failures(setup):
    app, _, _, _, _ = setup
    client = TestClient(
        app, base_url="https://testserver", headers={"X-EFM-Request": "1"}
    )
    for _ in range(8):
        assert (
            client.post(
                "/api/login", json={"username": "missing", "password": "incorrect"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/login", json={"username": "missing", "password": "incorrect"}
        ).status_code
        == 429
    )
    client.close()


def test_cross_lab_data_never_appears(setup):
    _, c, lab, p, _ = setup
    a, b = c["alice"], c["bob"]
    otherlab = b.post("/api/labs", json={"name": "Other private lab"}).json()["id"]
    otherproject = b.post(
        f"/api/labs/{otherlab}/projects", json={"name": "Private"}
    ).json()["id"]
    r = add(b, otherproject, experiment(title="CROSSLABSECRET"))
    assert a.get("/api/search?q=CROSSLABSECRET").json() == []
    assert a.get(f"/api/records/{r['id']}").status_code == 404
    assert a.post(f"/api/projects/{otherproject}/export").status_code == 404
