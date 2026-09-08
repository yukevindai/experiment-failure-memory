import csv
import io
import json
import zipfile

import pytest
from conftest import add

from failure_memory.cli import main, restore
from failure_memory.db import Database, sha
from failure_memory.exports import strict_json, verify_bundle


def test_export_roundtrip_archive_groups_and_quantity_units(setup):
    _, c, _, p, _ = setup
    a = c["alice"]
    one, middle, last = [add(a, p) for _ in range(3)]
    for source, target in ((middle, one), (last, middle)):
        a.post(
            f"/api/records/{source['id']}/links",
            json={"target_id": target["id"], "relation": "repeat_of"},
        )
    a.post(
        f"/api/records/{middle['id']}/archive",
        json={"expected_version": 1, "archived": True},
    )
    attachment = a.post(
        f"/api/records/{one['id']}/attachments?filename=demo.txt", content=b"SYNTHETIC"
    ).json()
    response = a.post(f"/api/projects/{p}/export?attachments=true")
    assert response.status_code == 200, response.text
    manifest, records = verify_bundle(response.content)
    assert manifest["records"] == 2 and not manifest["includes_archived"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        rows = list(csv.DictReader(io.StringIO(z.read("experiments.csv").decode())))
        assert rows[0]["attempt_group"] == rows[1]["attempt_group"]
        assert z.read(f"attachments/{attachment['id']}.bin") == b"SYNTHETIC"
        for q in manifest["quantities"]:
            assert (
                json.loads(z.read(q["auditor_config"]))["units"]["value"]["column"]
                == "unit"
            )
    public_copy = a.post(f"/api/projects/{p}/export").content
    with zipfile.ZipFile(io.BytesIO(public_copy)) as z:
        assert not any(n.startswith("attachments/") for n in z.namelist())
    verify_bundle(public_copy)


def rewrite(raw, replace):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        contents = {name: z.read(name) for name in z.namelist()}
    replace(contents)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data in contents.items():
            z.writestr(name, data)
    return out.getvalue()


def test_tampered_bundle_rejected_even_rehashed_table(setup):
    _, c, _, p, _ = setup
    a = c["alice"]
    add(a, p)
    raw = a.post(f"/api/projects/{p}/export").content
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_bundle(rewrite(raw, lambda d: d.update({"experiments.csv": b"changed"})))

    def malicious(data):
        data["experiments.csv"] = data["experiments.csv"].replace(
            b"Electrolyte precipitation", b"Fabricated result"
        )
        manifest = json.loads(data["manifest.json"])
        manifest["files"]["experiments.csv"] = sha(data["experiments.csv"])
        data["manifest.json"] = json.dumps(manifest).encode()

    with pytest.raises(ValueError, match="disagrees"):
        verify_bundle(rewrite(raw, malicious))
    with pytest.raises(ValueError, match="inventory"):
        verify_bundle(rewrite(raw, lambda d: d.update({"../outside": b"evil"})))
    for value in ('{"x":1,"x":2}', '{"x":NaN}'):
        with pytest.raises(ValueError):
            strict_json(value)


def test_backup_restore_retains_attachments_revokes_sessions(setup, tmp_path):
    app, c, _, p, _ = setup
    a = c["alice"]
    r = add(a, p)
    a.post(f"/api/records/{r['id']}/attachments?filename=x", content=b"backup-test")
    backup = tmp_path / "backup.sqlite"
    app.state.database.backup(backup)
    with pytest.raises(FileExistsError):
        app.state.database.backup(backup)
    target = tmp_path / "restored.sqlite"
    restore(backup, target)
    with Database(target).transaction() as db:
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert (
            db.execute("SELECT content FROM attachments").fetchone()[0]
            == b"backup-test"
        )
        assert db.execute("SELECT count(*) FROM records").fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        restore(backup, target)
    assert target.stat().st_mode & 0o777 == 0o600


def test_operator_cli_account_lifecycle(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli.sqlite"
    base = ["--database", str(path)]
    main(base + ["init"])
    monkeypatch.setenv("EFM_TEST_PASSWORD", "example-password-long")
    main(
        base
        + [
            "create-user",
            "alice",
            "--display-name",
            "Alice",
            "--password-env",
            "EFM_TEST_PASSWORD",
        ]
    )
    main(base + ["disable-user", "alice"])
    with Database(path).transaction() as db:
        assert db.execute("SELECT active FROM users").fetchone()[0] == 0
    main(base + ["enable-user", "alice"])
    main(base + ["reset-password", "alice", "--password-env", "EFM_TEST_PASSWORD"])
    assert "example-password-long" not in capsys.readouterr().out


def test_empty_export(setup):
    _, c, _, p, _ = setup
    manifest, records = verify_bundle(
        c["alice"].post(f"/api/projects/{p}/export").content
    )
    assert manifest["records"] == 0 and records == []
