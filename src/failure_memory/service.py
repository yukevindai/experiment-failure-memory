"""All record operations enforce project authorization inside their transaction."""

import json
import math
import re
from collections import Counter, defaultdict

from fastapi import HTTPException
from pint.errors import PintError

from .auth import project_access, visible_projects
from .db import canonical, event, now, uid
from .models import UNITS, Experiment


def unpack(row):
    result = dict(row)
    result["record"] = json.loads(result.pop("payload"))
    result["archived"] = bool(result["archived"])
    return result


def get_record(db, user, record_id, needed="viewer"):
    row = db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Experiment not found")
    project_access(db, user, row["project_id"], needed)
    return unpack(row)


def create_record(db, user, project_id, payload):
    project_access(db, user, project_id, "editor")
    payload = Experiment.model_validate(payload).model_dump(mode="json")
    record_id, at = uid(), now()
    encoded = canonical(payload)
    db.execute(
        "INSERT INTO records VALUES(?,?,1,?,0,?,?,?)",
        (record_id, project_id, encoded, user, at, at),
    )
    db.execute(
        "INSERT INTO revisions VALUES(?,1,?,0,?,?)", (record_id, encoded, user, at)
    )
    event(
        db,
        user,
        "record.created",
        record_id,
        project_id=project_id,
        detail={"version": 1},
    )
    return get_record(db, user, record_id)


def revise(db, user, record_id, expected, payload=None, archived=None):
    current = get_record(db, user, record_id, "editor")
    if current["version"] != expected:
        raise HTTPException(
            409, "Record changed; reload and review the latest version before saving"
        )
    payload = (
        current["record"]
        if payload is None
        else Experiment.model_validate(payload).model_dump(mode="json")
    )
    archived = current["archived"] if archived is None else archived
    version, at = expected + 1, now()
    encoded = canonical(payload)
    db.execute(
        "UPDATE records SET version=?,payload=?,archived=?,updated_at=? WHERE id=? AND version=?",
        (version, encoded, int(archived), at, record_id, expected),
    )
    db.execute(
        "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
        (record_id, version, encoded, int(archived), user, at),
    )
    event(
        db,
        user,
        "record.revised",
        record_id,
        project_id=current["project_id"],
        detail={"version": version, "archived": archived},
    )
    return get_record(db, user, record_id)


def link_records(db, user, record_id, target_id, relation):
    source = get_record(db, user, record_id, "editor")
    target = get_record(db, user, target_id)
    if source["project_id"] != target["project_id"]:
        raise HTTPException(400, "Relationships must stay within one project")
    pending, seen = [target_id], set()
    while pending:
        current = pending.pop()
        if current == record_id:
            raise HTTPException(409, "Attempt relationships cannot create a cycle")
        if current not in seen:
            seen.add(current)
            pending.extend(
                r[0]
                for r in db.execute(
                    "SELECT target_id FROM links WHERE source_id=?", (current,)
                )
            )
    existing = db.execute(
        "SELECT * FROM links WHERE source_id=? AND target_id=? AND relation=?",
        (record_id, target_id, relation),
    ).fetchone()
    if existing:
        return dict(existing)
    link = dict(
        id=uid(),
        source_id=record_id,
        target_id=target_id,
        relation=relation,
        actor_id=user,
        at=now(),
    )
    db.execute(
        "INSERT INTO links VALUES(:id,:source_id,:target_id,:relation,:actor_id,:at)",
        link,
    )
    event(
        db,
        user,
        "record.linked",
        record_id,
        project_id=source["project_id"],
        detail=link,
    )
    return link


def record_detail(db, user, record_id):
    result = get_record(db, user, record_id)
    for field, sql in {
        "comments": "SELECT c.*,u.display_name AS author FROM comments c JOIN users u ON u.id=c.actor_id WHERE c.record_id=? ORDER BY c.at,c.id",
        "attachments": "SELECT id,record_id,filename,sha256,size,actor_id,at FROM attachments WHERE record_id=? ORDER BY at,id",
        "history": "SELECT r.*,u.display_name AS author FROM revisions r JOIN users u ON u.id=r.actor_id WHERE record_id=? ORDER BY version",
    }.items():
        result[field] = [dict(r) for r in db.execute(sql, (record_id,))]
    for revision in result["history"]:
        revision["record"] = json.loads(revision.pop("payload"))
    result["links"] = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM links WHERE source_id=? OR target_id=? ORDER BY at,id",
            (record_id, record_id),
        )
    ]
    return result


def permitted_records(db, user, project_id=None, archived=False):
    ids = (
        [project_access(db, user, project_id)["id"]]
        if project_id
        else [p["id"] for p in visible_projects(db, user)]
    )
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT * FROM records WHERE project_id IN ({placeholders}) AND archived=? ORDER BY updated_at DESC,id LIMIT 10001",
        (*ids, int(archived)),
    ).fetchall()
    if len(rows) > 10000:
        raise HTTPException(
            413,
            "Search scope exceeds 10000 records; select a smaller project or archive old records",
        )
    return [unpack(r) for r in rows]


def words(value):
    return set(re.findall(r"\w+", value.casefold()))


def record_words(record):
    p = record["record"]
    return words(
        " ".join(
            [
                p["title"],
                p["summary"],
                p["outcomes"],
                p["uncertainty_notes"],
                *p["tags"],
                *p["procedure"],
                *(m["name"] for m in p["materials"]),
                *(e["name"] for e in p["equipment"]),
                *(c["cause"] for c in p["suspected_causes"]),
                *(f["action"] for f in p["fixes"]),
            ]
        )
    )


def search(db, user, query="", project_id=None, status=None, archived=False, limit=50):
    tokens = words(query)
    matches = []
    for record in permitted_records(db, user, project_id, archived):
        if status and record["record"]["status"] != status:
            continue
        matched = sorted(tokens & record_words(record))
        if tokens and not matched:
            continue
        matches.append(
            dict(
                record, matched_terms=matched, score=len(matched) / max(1, len(tokens))
            )
        )
    return sorted(matches, key=lambda r: r["score"], reverse=True)[:limit]


def similar(db, user, record_id, limit=10):
    original = get_record(db, user, record_id)
    tokens = record_words(original)
    values = {q["name"].casefold(): q for q in original["record"]["conditions"]}
    found = []
    for candidate in permitted_records(db, user, original["project_id"]):
        if candidate["id"] == record_id:
            continue
        other = record_words(candidate)
        lexical = len(tokens & other) / max(1, len(tokens | other))
        compared = []
        for q in candidate["record"]["conditions"]:
            ref = values.get(q["name"].casefold())
            if ref:
                try:
                    base = UNITS.Quantity(ref["value"], ref["unit"]).to_base_units()
                    value = (
                        UNITS.Quantity(q["value"], q["unit"]).to(base.units).magnitude
                    )
                    scale = max(abs(value), abs(base.magnitude), 1e-12)
                    if not all(
                        math.isfinite(v) for v in (value, base.magnitude, scale)
                    ):
                        continue
                    distance = abs(value / scale - base.magnitude / scale)
                    compared.append(
                        {
                            "name": q["name"],
                            "reference_value": base.magnitude,
                            "candidate_value": value,
                            "unit": str(base.units),
                            "relative_distance": distance,
                        }
                    )
                except (ValueError, TypeError, ArithmeticError, PintError):
                    continue
        closeness = (
            sum(1 / (1 + q["relative_distance"]) for q in compared) / len(compared)
            if compared
            else 0
        )
        score = lexical if not compared else 0.7 * lexical + 0.3 * closeness
        found.append(
            dict(
                candidate,
                similarity=score,
                matched_terms=sorted(tokens & other),
                condition_comparisons=compared,
                interpretation="Descriptive lexical/condition similarity, not evidence of a shared cause.",
            )
        )
    return sorted(found, key=lambda r: (-r["similarity"], r["id"]))[:limit]


def patterns(db, user, project_id):
    records = permitted_records(db, user, project_id)
    causes, fixes = defaultdict(list), defaultdict(list)
    for r in records:
        for c in r["record"]["suspected_causes"]:
            causes[" ".join(c["cause"].casefold().split())].append(
                {"record_id": r["id"], **c}
            )
        for f in r["record"]["fixes"]:
            fixes[" ".join(f["action"].casefold().split())].append(
                {"record_id": r["id"], **f}
            )
    return {
        "record_count": len(records),
        "outcomes": dict(Counter(r["record"]["status"] for r in records)),
        "causes": [
            {
                "label": k,
                "record_count": len({r["record_id"] for r in v}),
                "evidence": v,
            }
            for k, v in sorted(causes.items(), key=lambda kv: -len(kv[1]))
        ],
        "recovery_strategies": [
            {
                "action": k,
                "reported_outcomes": dict(Counter(f["outcome"] for f in v)),
                "evidence": v,
            }
            for k, v in sorted(fixes.items())
        ],
        "interpretation": "Case/whitespace-normalized self-reports, not causal effects or independent trials. Related attempts and selective recording can bias apparent recovery rates.",
    }
