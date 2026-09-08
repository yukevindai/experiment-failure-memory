"""Explicit offline bridge to Scientific Evidence Engine's PDF ingestion API."""

import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

from .db import canonical, sha
from .exports import verify_bundle


def render_record(record, bundle_digest, destination):
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    body = [
        Paragraph(
            "Internal experimental evidence — researcher supplied", styles["Title"]
        ),
        Paragraph(
            "This is a rendered database record, not a published paper or an independent verification. Suspected causes and recovery outcomes are self-reports.",
            styles["BodyText"],
        ),
        Spacer(1, 12),
    ]
    # ASCII JSON escapes preserve every original Unicode codepoint even with base PDF fonts.
    # Each line is bounded to avoid layout overflow; the authoritative JSON is retained alongside.
    text = json.dumps(
        {"source_bundle_sha256": bundle_digest, "experiment": record},
        ensure_ascii=True,
        indent=2,
    )
    for line in text.splitlines():
        for offset in range(0, max(1, len(line)), 95):
            body.append(
                Paragraph(escape(line[offset : offset + 95]) or " ", styles["Code"])
            )

    def canvas(*args, **kwargs):
        kwargs["invariant"] = 1
        return Canvas(*args, **kwargs)

    SimpleDocTemplate(
        str(destination),
        title="Internal experimental evidence",
        author="Experiment Failure Memory",
    ).build(body, canvasmaker=canvas)


def import_evidence(bundle, output, store):
    try:
        import reportlab  # noqa: F401
        from scientific_evidence_engine import index_paper, ingest_paper
    except ImportError as exc:
        raise ValueError(
            "Install the integration extra and Scientific Evidence Engine 0.2 before importing evidence"
        ) from exc
    raw = Path(bundle).read_bytes()
    manifest, records = verify_bundle(raw)
    destination = Path(output).resolve()
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    # A new store prevents mixing private project exports into an existing evidence collection.
    store = Path(store).resolve()
    store.mkdir(parents=True, exist_ok=False, mode=0o700)
    result = {
        "schema_version": "1.0",
        "kind": "internal_experimental_evidence_bridge",
        "source_bundle_sha256": sha(raw),
        "project_id": manifest["project_id"],
        "records": [],
        "interpretation": "Exact passages from rendered researcher records; human review is required before linking scientific claims.",
    }
    (destination / "source-export.zip").write_bytes(raw)
    (destination / "records.json").write_text(canonical(records), encoding="utf-8")
    for r in records:
        # Hash identity rather than trusting a path supplied in an imported record.
        pdf = destination / (sha(r["id"].encode()) + ".pdf")
        render_record(r, sha(raw), pdf)
        paper = ingest_paper(
            pdf,
            store,
            {
                "title": "Internal experiment: " + r["record"]["title"],
                "notes": canonical(
                    {
                        "source_kind": "internal_experiment",
                        "record_id": r["id"],
                        "version": r["version"],
                        "source_reference": r["record"]["source"]["reference"],
                        "bundle_sha256": sha(raw),
                        "created_by": r["created_by"],
                    }
                ),
            },
        )
        indexed = index_paper(store, paper["paper_id"])
        result["records"].append(
            {
                "record_id": r["id"],
                "version": r["version"],
                "paper_id": paper["paper_id"],
                "pdf_sha256": sha(pdf.read_bytes()),
                "evidence_ids": indexed["evidence_ids"],
            }
        )
        # Checkpoint mappings so partial imports remain inspectable if the next record fails.
        (destination / "bridge-manifest.json").write_text(
            canonical(result), encoding="utf-8"
        )
    (destination / "bridge-manifest.json").write_text(
        canonical(result), encoding="utf-8"
    )
    for path in destination.iterdir():
        if path.is_file():
            os.chmod(path, 0o600)
    return result
