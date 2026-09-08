import hmac
import re
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import models, service
from .auth import (
    authenticate,
    lab_access,
    login,
    project_access,
    username,
    visible_projects,
)
from .db import Database, canonical, event, now, sha, uid
from .exports import export_project
from .limits import BodyLimit


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


def create_app(
    database_path="data/memory.sqlite",
    origin="https://localhost:8000",
    secure_cookies=True,
):
    parsed = urlparse(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
    ):
        raise ValueError("Configure a single absolute public origin without a path")
    if not secure_cookies and (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError(
            "Insecure cookies are allowed only for loopback HTTP development"
        )
    if secure_cookies and parsed.scheme != "https":
        raise ValueError("Secure deployment requires an HTTPS origin")
    origin = origin.rstrip("/")
    database = Database(database_path)
    database.initialize()
    app = FastAPI(
        title="Experiment Failure Memory",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.database = database

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Never echo passwords, private record payloads, or non-finite input into errors.
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ]
            },
            status_code=422,
        )

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname])

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("X-EFM-Request") != "1" or (
                request.headers.get("origin") and request.headers["origin"] != origin
            ):
                return JSONResponse(
                    {"detail": "Cross-origin or missing application request header"},
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        if secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def connection():
        with database.transaction() as db:
            yield db

    def current(request: Request, db=Depends(connection)):
        user = authenticate(db, request.cookies.get("efm_session"))
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not hmac.compare_digest(
            request.headers.get("X-CSRF-Token", "").encode(), user["csrf"].encode()
        ):
            raise HTTPException(403, "Invalid CSRF token")
        return user

    @app.post("/api/login")
    def sign_in(payload: Login, request: Request, db=Depends(connection)):
        result, status = login(
            db,
            payload.username,
            payload.password,
            request.client.host if request.client else "unknown",
        )
        if result is None:
            return JSONResponse(
                {
                    "detail": "Too many login attempts; try again later"
                    if status == 429
                    else "Invalid username or password"
                },
                status_code=status,
            )
        old = request.cookies.get("efm_session")
        if old:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (sha(old.encode()),))
        response = JSONResponse({"user": result["user"], "csrf": result["csrf"]})
        response.set_cookie(
            "efm_session",
            result["token"],
            httponly=True,
            secure=secure_cookies,
            samesite="strict",
            max_age=43200,
            path="/",
        )
        return response

    @app.get("/api/me")
    def me(user=Depends(current)):
        return user

    @app.post("/api/logout")
    def logout(request: Request, user=Depends(current), db=Depends(connection)):
        db.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (sha(request.cookies["efm_session"].encode()),),
        )
        response = JSONResponse({"ok": True})
        response.delete_cookie(
            "efm_session",
            path="/",
            secure=secure_cookies,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get("/api/labs")
    def labs(user=Depends(current), db=Depends(connection)):
        return [
            dict(r)
            for r in db.execute(
                "SELECT l.*,m.role FROM labs l JOIN lab_members m ON m.lab_id=l.id WHERE m.user_id=? ORDER BY l.name",
                (user["id"],),
            )
        ]

    @app.post("/api/labs", status_code=201)
    def create_lab(payload: models.Name, user=Depends(current), db=Depends(connection)):
        lab_id = uid()
        db.execute("INSERT INTO labs VALUES(?,?,?)", (lab_id, payload.name, user["id"]))
        db.execute("INSERT INTO lab_members VALUES(?,?,'owner')", (lab_id, user["id"]))
        event(db, user["id"], "lab.created", lab_id, lab_id=lab_id)
        return {"id": lab_id, "name": payload.name}

    @app.get("/api/labs/{lab_id}/members")
    def lab_members(lab_id: str, user=Depends(current), db=Depends(connection)):
        lab_access(db, user["id"], lab_id, admin=True)
        return [
            dict(r)
            for r in db.execute(
                "SELECT u.username,u.display_name,m.role FROM lab_members m JOIN users u ON u.id=m.user_id WHERE lab_id=?",
                (lab_id,),
            )
        ]

    @app.put("/api/labs/{lab_id}/members")
    def grant_lab(
        lab_id: str,
        payload: models.Member,
        user=Depends(current),
        db=Depends(connection),
    ):
        role = lab_access(db, user["id"], lab_id, admin=True)
        if payload.role not in {"admin", "member", "remove"}:
            raise HTTPException(422, "Role must be admin, member or remove")
        target = db.execute(
            "SELECT id FROM users WHERE username=? AND active=1",
            (username(payload.username),),
        ).fetchone()
        if not target:
            raise HTTPException(
                404, "Account not found; ask the server operator to provision it"
            )
        previous = db.execute(
            "SELECT role FROM lab_members WHERE lab_id=? AND user_id=?",
            (lab_id, target["id"]),
        ).fetchone()
        if previous and previous["role"] == "owner":
            raise HTTPException(
                409, "The laboratory owner cannot be removed or demoted"
            )
        if role != "owner" and (
            payload.role == "admin" or (previous and previous["role"] == "admin")
        ):
            raise HTTPException(
                403, "Only the owner can manage laboratory administrators"
            )
        if payload.role == "remove":
            db.execute(
                "DELETE FROM project_members WHERE user_id=? AND project_id IN (SELECT id FROM projects WHERE lab_id=?)",
                (target["id"], lab_id),
            )
            db.execute(
                "DELETE FROM lab_members WHERE lab_id=? AND user_id=?",
                (lab_id, target["id"]),
            )
        else:
            db.execute(
                "INSERT INTO lab_members VALUES(?,?,?) ON CONFLICT(lab_id,user_id) DO UPDATE SET role=excluded.role",
                (lab_id, target["id"], payload.role),
            )
        event(
            db,
            user["id"],
            "lab.member_changed",
            target["id"],
            lab_id=lab_id,
            detail={"role": payload.role},
        )
        return {"ok": True}

    @app.get("/api/projects")
    def projects(user=Depends(current), db=Depends(connection)):
        return visible_projects(db, user["id"])

    @app.post("/api/labs/{lab_id}/projects", status_code=201)
    def create_project(
        lab_id: str,
        payload: models.Project,
        user=Depends(current),
        db=Depends(connection),
    ):
        lab_access(db, user["id"], lab_id, admin=True)
        project_id = uid()
        db.execute(
            "INSERT INTO projects VALUES(?,?,?,?)",
            (project_id, lab_id, payload.name, payload.description),
        )
        event(
            db,
            user["id"],
            "project.created",
            project_id,
            lab_id=lab_id,
            project_id=project_id,
        )
        return {"id": project_id, "name": payload.name}

    @app.get("/api/projects/{project_id}/members")
    def project_members(project_id: str, user=Depends(current), db=Depends(connection)):
        project_access(db, user["id"], project_id, "admin")
        return [
            dict(r)
            for r in db.execute(
                "SELECT u.username,u.display_name,m.role FROM project_members m JOIN users u ON u.id=m.user_id WHERE project_id=?",
                (project_id,),
            )
        ]

    @app.put("/api/projects/{project_id}/members")
    def grant_project(
        project_id: str,
        payload: models.Member,
        user=Depends(current),
        db=Depends(connection),
    ):
        project = project_access(db, user["id"], project_id, "admin")
        if payload.role not in {"viewer", "editor", "admin", "remove"}:
            raise HTTPException(422, "Invalid project role")
        target = db.execute(
            "SELECT u.id FROM users u JOIN lab_members m ON m.user_id=u.id WHERE u.username=? AND m.lab_id=? AND u.active=1",
            (username(payload.username), project["lab_id"]),
        ).fetchone()
        if not target:
            raise HTTPException(404, "Account is not a member of this laboratory")
        if payload.role == "remove":
            db.execute(
                "DELETE FROM project_members WHERE project_id=? AND user_id=?",
                (project_id, target["id"]),
            )
        else:
            db.execute(
                "INSERT INTO project_members VALUES(?,?,?) ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role",
                (project_id, target["id"], payload.role),
            )
        event(
            db,
            user["id"],
            "project.member_changed",
            target["id"],
            project_id=project_id,
            detail={"role": payload.role},
        )
        return {"ok": True}

    @app.get("/api/search")
    def search(
        q: str = Query(default="", max_length=1000),
        project_id: str | None = None,
        status: str | None = None,
        archived: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
        user=Depends(current),
        db=Depends(connection),
    ):
        if status and status not in {"failed", "partial", "succeeded", "inconclusive"}:
            raise HTTPException(422, "Unknown outcome status")
        return service.search(db, user["id"], q, project_id, status, archived, limit)

    @app.post("/api/projects/{project_id}/records", status_code=201)
    def create_record(
        project_id: str,
        payload: models.Experiment,
        user=Depends(current),
        db=Depends(connection),
    ):
        return service.create_record(
            db, user["id"], project_id, payload.model_dump(mode="json")
        )

    @app.get("/api/records/{record_id}")
    def record(record_id: str, user=Depends(current), db=Depends(connection)):
        return service.record_detail(db, user["id"], record_id)

    @app.put("/api/records/{record_id}")
    def edit_record(
        record_id: str,
        payload: models.Edit,
        user=Depends(current),
        db=Depends(connection),
    ):
        return service.revise(
            db,
            user["id"],
            record_id,
            payload.expected_version,
            payload.record.model_dump(mode="json"),
        )

    @app.post("/api/records/{record_id}/archive")
    def archive(
        record_id: str,
        payload: models.Archive,
        user=Depends(current),
        db=Depends(connection),
    ):
        return service.revise(
            db,
            user["id"],
            record_id,
            payload.expected_version,
            archived=payload.archived,
        )

    @app.post("/api/records/{record_id}/comments", status_code=201)
    def comment(
        record_id: str,
        payload: models.Comment,
        user=Depends(current),
        db=Depends(connection),
    ):
        record = service.get_record(db, user["id"], record_id, "editor")
        comment = {
            "id": uid(),
            "record_id": record_id,
            "body": payload.body,
            "actor_id": user["id"],
            "at": now(),
        }
        db.execute(
            "INSERT INTO comments VALUES(:id,:record_id,:body,:actor_id,:at)", comment
        )
        event(
            db,
            user["id"],
            "comment.added",
            comment["id"],
            project_id=record["project_id"],
        )
        return comment

    @app.post("/api/records/{record_id}/links", status_code=201)
    def link(
        record_id: str,
        payload: models.Link,
        user=Depends(current),
        db=Depends(connection),
    ):
        return service.link_records(
            db, user["id"], record_id, payload.target_id, payload.relation
        )

    @app.get("/api/records/{record_id}/similar")
    def similar(record_id: str, user=Depends(current), db=Depends(connection)):
        return service.similar(db, user["id"], record_id)

    @app.get("/api/projects/{project_id}/patterns")
    def patterns(project_id: str, user=Depends(current), db=Depends(connection)):
        return service.patterns(db, user["id"], project_id)

    @app.post("/api/records/{record_id}/attachments", status_code=201)
    async def upload(
        record_id: str,
        request: Request,
        filename: str = Query(min_length=1, max_length=200),
        user=Depends(current),
        db=Depends(connection),
    ):
        record = service.get_record(db, user["id"], record_id, "editor")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16 * 1024 * 1024:
                raise HTTPException(413, "Attachment exceeds 16 MiB")
        if not raw:
            raise HTTPException(422, "Attachment cannot be empty")
        filename = re.sub(
            r"[\x00-\x1f\x7f]", "", filename.replace("\\", "/").split("/")[-1]
        )
        if not filename:
            raise HTTPException(422, "Invalid filename")
        attachment = {
            "id": uid(),
            "record_id": record_id,
            "filename": filename,
            "sha256": sha(raw),
            "size": len(raw),
            "actor_id": user["id"],
            "at": now(),
        }
        db.execute(
            "INSERT INTO attachments VALUES(:id,:record_id,:filename,:sha256,:size,:content,:actor_id,:at)",
            dict(attachment, content=bytes(raw)),
        )
        event(
            db,
            user["id"],
            "attachment.added",
            attachment["id"],
            project_id=record["project_id"],
            detail={"sha256": attachment["sha256"]},
        )
        return attachment

    @app.get("/api/attachments/{attachment_id}")
    def download(attachment_id: str, user=Depends(current), db=Depends(connection)):
        row = db.execute(
            "SELECT * FROM attachments WHERE id=?", (attachment_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Attachment not found")
        record = service.get_record(db, user["id"], row["record_id"])
        if sha(row["content"]) != row["sha256"]:
            raise HTTPException(409, "Attachment integrity check failed")
        event(
            db,
            user["id"],
            "attachment.downloaded",
            attachment_id,
            project_id=record["project_id"],
        )
        return Response(
            row["content"],
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(row["filename"], safe="")
            },
        )

    @app.get("/api/projects/{project_id}/events")
    def events(project_id: str, user=Depends(current), db=Depends(connection)):
        project_access(db, user["id"], project_id, "admin")
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM events WHERE project_id=? ORDER BY id DESC LIMIT 500",
                (project_id,),
            )
        ]

    @app.post("/api/projects/{project_id}/export")
    def export(
        project_id: str,
        attachments: bool = False,
        user=Depends(current),
        db=Depends(connection),
    ):
        return Response(
            export_project(db, user["id"], project_id, attachments),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="experiment-memory.zip"'
            },
        )

    @app.post("/api/projects/{project_id}/import")
    def import_record(
        project_id: str,
        payload: models.Import,
        user=Depends(current),
        db=Depends(connection),
    ):
        project_access(db, user["id"], project_id, "editor")
        encoded = canonical(payload.model_dump(mode="json"))
        digest = sha(encoded.encode())
        existing = db.execute(
            "SELECT * FROM imports WHERE project_id=? AND connector=? AND external_id=?",
            (project_id, payload.connector, payload.external_id),
        ).fetchone()
        if existing:
            if existing["payload_sha256"] != digest:
                raise HTTPException(
                    409,
                    "External record changed; review an explicit revision instead of overwriting",
                )
            return {
                "record": service.get_record(db, user["id"], existing["record_id"]),
                "replayed": True,
            }
        record = service.create_record(
            db, user["id"], project_id, payload.record.model_dump(mode="json")
        )
        db.execute(
            "INSERT INTO imports VALUES(?,?,?,?,?)",
            (project_id, payload.connector, payload.external_id, digest, record["id"]),
        )
        event(
            db,
            user["id"],
            "eln.imported",
            record["id"],
            project_id=project_id,
            detail={
                "connector": payload.connector,
                "external_id": payload.external_id,
                "payload_sha256": digest,
            },
        )
        return {"record": record, "replayed": False}

    app.add_middleware(BodyLimit)
    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
