import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Database:
    def __init__(self, path):
        self.path = Path(path).resolve()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        os.chmod(self.path, 0o600)
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(Path(__file__).with_name("schema.sql").read_text())
            versions = [r[0] for r in db.execute("SELECT version FROM schema_version")]
            if versions != [1]:
                raise RuntimeError(
                    "Unsupported database schema; explicit migration required"
                )
        os.chmod(self.path, 0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            # Short serialized transactions keep permission changes and data writes atomic.
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def backup(self, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with destination.open("xb"):
            pass
        os.chmod(destination, 0o600)
        try:
            with (
                sqlite3.connect(self.path) as source,
                sqlite3.connect(destination) as target,
            ):
                source.backup(target)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


def event(db, actor, action, subject, lab_id=None, project_id=None, detail=None):
    db.execute(
        "INSERT INTO events(lab_id,project_id,actor_id,action,subject_id,detail,at) VALUES(?,?,?,?,?,?,?)",
        (lab_id, project_id, actor, action, subject, canonical(detail or {}), now()),
    )
