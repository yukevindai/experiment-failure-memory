"""Optional real upstream integration checks; no mocks of the upstream parsers."""

import io
import json
import zipfile

import pytest
from conftest import add


def test_actual_chemdata_auditor_accepts_export(setup):
    auditor = pytest.importorskip("chemdata_auditor")
    import pandas as pd

    _, c, _, p, _ = setup
    add(c["alice"], p)
    with zipfile.ZipFile(
        io.BytesIO(c["alice"].post(f"/api/projects/{p}/export").content)
    ) as z:
        manifest = json.loads(z.read("manifest.json"))
        pairs = [("experiments.csv", "auditor-config.json")] + [
            (q["csv"], q["auditor_config"]) for q in manifest["quantities"]
        ]
        for csv_path, config_path in pairs:
            config = auditor.AuditConfig(**json.loads(z.read(config_path)))
            report = auditor.audit(pd.read_csv(io.BytesIO(z.read(csv_path))), config)
            assert report is not None


def test_actual_evidence_engine_indexes_internal_record(setup, tmp_path):
    pytest.importorskip("scientific_evidence_engine")
    pytest.importorskip("reportlab")
    from failure_memory.evidence import import_evidence

    _, c, _, p, _ = setup
    r = add(c["alice"], p)
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(c["alice"].post(f"/api/projects/{p}/export").content)
    result = import_evidence(bundle, tmp_path / "rendered", tmp_path / "evidence-store")
    assert result["records"][0]["record_id"] == r["id"]
    assert result["records"][0]["evidence_ids"]
    pages = list((tmp_path / "evidence-store" / "papers").glob("*/pages/*.txt"))
    text = "\n".join(page.read_text() for page in pages)
    assert "researcher supplied" in " ".join(text.split()) and "SYNTHETIC" in text
    assert (
        tmp_path / "rendered" / "source-export.zip"
    ).read_bytes() == bundle.read_bytes()
