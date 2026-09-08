"""Explicit project-scoped exports. No automatic outbound transfer or public links."""

import csv
import io
import json
import math
import zipfile
from collections import defaultdict

from fastapi import HTTPException

from .auth import project_access
from .db import canonical, event, now, sha
from .models import Experiment
from .service import permitted_records, record_detail

MAX_EXPORT = 64 * 1024 * 1024


def csv_data(rows, columns):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def export_project(db, user, project_id, include_attachments=False):
    project = project_access(db, user, project_id)
    records = [
        record_detail(db, user, r["id"])
        for r in permitted_records(db, user, project_id)
    ]
    parent = {
        r["id"]: r["id"]
        for r in db.execute("SELECT id FROM records WHERE project_id=?", (project_id,))
    }

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    # Archived intermediate attempts still connect active experiments into one group.
    for link in db.execute(
        "SELECT l.* FROM links l JOIN records r ON r.id=l.source_id WHERE r.project_id=?",
        (project_id,),
    ):
        a, b = link["source_id"], link["target_id"]
        if a in parent and b in parent:
            ra, rb = root(a), root(b)
            parent[max(ra, rb)] = min(ra, rb)
    table, quantities = [], defaultdict(list)
    files = {"records.json": canonical(records).encode()}
    budget = len(files["records.json"])
    for r in records:
        p = r["record"]
        table.append(
            {
                "record_id": r["id"],
                "version": r["version"],
                "project_id": project_id,
                "attempt_group": root(r["id"]),
                "title": p["title"],
                "status": p["status"],
                "performed_at": p["performed_at"],
                "source_reference": p["source"]["reference"],
                "created_by": r["created_by"],
                "failed": 1
                if p["status"] == "failed"
                else 0
                if p["status"] == "succeeded"
                else "",
            }
        )
        for kind in ("conditions", "measurements"):
            for q in p[kind]:
                quantities[(kind, q["name"])].append(
                    {
                        "record_id": r["id"],
                        "attempt_group": root(r["id"]),
                        "value": q["value"],
                        "unit": q["unit"],
                        "uncertainty": q["uncertainty"]
                        if q["uncertainty"] is not None
                        else "",
                        "source_reference": p["source"]["reference"],
                    }
                )
        if include_attachments:
            for a in r["attachments"]:
                budget += a["size"]
                if budget > MAX_EXPORT - 1024 * 1024:
                    raise HTTPException(
                        413, "Export exceeds size budget; omit attachments"
                    )
                raw = db.execute(
                    "SELECT content FROM attachments WHERE id=?", (a["id"],)
                ).fetchone()[0]
                if sha(raw) != a["sha256"]:
                    raise HTTPException(409, "Attachment integrity check failed")
                files[f"attachments/{a['id']}.bin"] = raw
    columns = [
        "record_id",
        "version",
        "project_id",
        "attempt_group",
        "title",
        "status",
        "performed_at",
        "source_reference",
        "created_by",
        "failed",
    ]
    files["experiments.csv"] = csv_data(table, columns)
    files["auditor-config.json"] = canonical(
        {
            "numeric_columns": ["version", "failed"],
            "bounds": {"failed": [0, 1]},
            "duplicate_columns": ["record_id", "version"],
            "group_columns": ["attempt_group"],
            "provenance_columns": [
                "record_id",
                "project_id",
                "source_reference",
                "created_by",
                "performed_at",
            ],
        }
    ).encode()
    quantity_index = []
    for (kind, name), rows in quantities.items():
        key = sha(canonical([kind, name]).encode())
        path = f"quantities/{key}.csv"
        config = f"quantities/{key}.audit.json"
        files[path] = csv_data(
            rows,
            [
                "record_id",
                "attempt_group",
                "value",
                "unit",
                "uncertainty",
                "source_reference",
            ],
        )
        files[config] = canonical(
            {
                "numeric_columns": ["value", "uncertainty"],
                "bounds": {"uncertainty": [0, None]},
                "units": {"value": {"column": "unit", "expected": rows[0]["unit"]}},
                "duplicate_columns": ["record_id"],
                "group_columns": ["attempt_group"],
                "provenance_columns": ["record_id", "source_reference"],
            }
        ).encode()
        quantity_index.append(
            {"kind": kind, "name": name, "csv": path, "auditor_config": config}
        )
    if sum(map(len, files.values())) > MAX_EXPORT - 1024 * 1024:
        raise HTTPException(
            413, "Export exceeds 64 MiB; omit attachments or use a smaller project"
        )
    manifest = {
        "schema_version": "1.0",
        "kind": "internal_experimental_evidence",
        "lab_id": project["lab_id"],
        "project_id": project_id,
        "project_name": project["name"],
        "exported_by": user,
        "exported_at": now(),
        "records": len(records),
        "includes_archived": False,
        "includes_attachments": include_attachments,
        "files": {p: sha(raw) for p, raw in files.items()},
        "quantities": quantity_index,
        "limitations": [
            "Exported copies do not retain live access controls; share only with authorized recipients.",
            "Suspected causes, recovery results and uncertainties are researcher assertions, not verified causal effects.",
            "Attempt groups include archived intermediate links, but use recorded links only; missing links do not establish independence.",
            "failed is 1 for failed, 0 for succeeded, blank for partial/inconclusive; it is not a validated ML target.",
        ],
    }
    files["manifest.json"] = canonical(manifest).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, raw in files.items():
            archive.writestr(path, raw)
    event(
        db,
        user,
        "project.exported",
        project_id,
        project_id=project_id,
        detail={
            "records": len(records),
            "attachments": include_attachments,
            "sha256": sha(buffer.getvalue()),
        },
    )
    return buffer.getvalue()


def verify_bundle(raw):
    if len(raw) > MAX_EXPORT:
        raise ValueError("Bundle exceeds 64 MiB")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if (
            len(z.namelist()) != len(set(z.namelist()))
            or sum(i.file_size for i in z.infolist()) > MAX_EXPORT
        ):
            raise ValueError("Duplicate ZIP members or oversized expanded bundle")
        manifest = strict_json(z.read("manifest.json"))
        if (
            manifest.get("schema_version") != "1.0"
            or manifest.get("kind") != "internal_experimental_evidence"
        ):
            raise ValueError("Unsupported export schema")
        if set(z.namelist()) != set(manifest["files"]) | {"manifest.json"}:
            raise ValueError("Bundle file inventory mismatch")
        for path, digest in manifest["files"].items():
            if (
                path.startswith("/")
                or ".." in path.split("/")
                or "\\" in path
                or sha(z.read(path)) != digest
            ):
                raise ValueError("Unsafe path or content hash mismatch")
        records = strict_json(z.read("records.json"))
        if len(records) != manifest["records"] or any(
            r["project_id"] != manifest["project_id"] for r in records
        ):
            raise ValueError("Bundle record scope/count mismatch")
        ids = set()
        for record in records:
            if (
                record["id"] in ids
                or type(record["version"]) is not int
                or record["version"] < 1
            ):
                raise ValueError("Invalid record identity/version")
            ids.add(record["id"])
            Experiment.model_validate(record["record"])
            if (
                not record["history"]
                or record["history"][-1]["version"] != record["version"]
                or record["history"][-1]["record"] != record["record"]
            ):
                raise ValueError("Current record differs from revision history")
            for attachment in record["attachments"]:
                path = f"attachments/{attachment['id']}.bin"
                if manifest["includes_attachments"]:
                    content = z.read(path)
                    if (
                        sha(content) != attachment["sha256"]
                        or len(content) != attachment["size"]
                    ):
                        raise ValueError("Attachment metadata mismatch")
        rows = list(csv.DictReader(io.StringIO(z.read("experiments.csv").decode())))
        if len(rows) != len(records) or {row["record_id"] for row in rows} != ids:
            raise ValueError("Experiment table identity mismatch")
        by_id = {record["id"]: record for record in records}
        for row in rows:
            record = by_id[row["record_id"]]
            p = record["record"]
            expected = dict(
                version=str(record["version"]),
                project_id=record["project_id"],
                title=p["title"],
                status=p["status"],
                performed_at=p["performed_at"],
                source_reference=p["source"]["reference"],
                created_by=record["created_by"],
                failed="1"
                if p["status"] == "failed"
                else "0"
                if p["status"] == "succeeded"
                else "",
            )
            if any(row[key] != value for key, value in expected.items()):
                raise ValueError("Experiment table disagrees with record evidence")
        expected_quantities = defaultdict(list)
        groups = {row["record_id"]: row["attempt_group"] for row in rows}
        for record in records:
            for kind in ("conditions", "measurements"):
                for q in record["record"][kind]:
                    expected_quantities[(kind, q["name"])].append(
                        {
                            "record_id": record["id"],
                            "attempt_group": groups[record["id"]],
                            "value": q["value"],
                            "unit": q["unit"],
                            "uncertainty": q["uncertainty"]
                            if q["uncertainty"] is not None
                            else "",
                            "source_reference": record["record"]["source"]["reference"],
                        }
                    )
        keys = [(item["kind"], item["name"]) for item in manifest["quantities"]]
        if len(keys) != len(set(keys)) or set(keys) != set(expected_quantities):
            raise ValueError("Quantity index mismatch")
        for item in manifest["quantities"]:
            expected_rows = expected_quantities[(item["kind"], item["name"])]
            expected_csv = csv_data(
                expected_rows,
                [
                    "record_id",
                    "attempt_group",
                    "value",
                    "unit",
                    "uncertainty",
                    "source_reference",
                ],
            )
            if z.read(item["csv"]) != expected_csv:
                raise ValueError("Quantity table disagrees with record evidence")
        return manifest, records


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Non-finite JSON number")

    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("Non-finite JSON number")
        return result

    return json.loads(
        raw, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite
    )
