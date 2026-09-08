import hashlib
import hmac
import re
import secrets
import time

from fastapi import HTTPException

from .db import sha, uid


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("Use a password with 12–1024 characters")
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
    return salt.hex() + ":" + key.hex()


def verify_password(password, encoded):
    try:
        salt, expected = encoded.split(":")
        key = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=64
        )
        return hmac.compare_digest(key.hex(), expected)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = password_hash("not-an-actual-account-password")


def username(value):
    value = value.strip().casefold()
    if not re.fullmatch(r"[a-z0-9_.-]{3,80}", value):
        raise ValueError(
            "Usernames need 3–80 letters, digits, dots, underscores or hyphens"
        )
    return value


def create_user(database, name, display_name, password):
    name = username(name)
    if not display_name.strip() or len(display_name) > 200:
        raise ValueError("Display name must contain 1–200 characters")
    hashed = password_hash(password)
    with database.transaction() as db:
        if db.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
            raise ValueError("Username already exists")
        user_id = uid()
        db.execute(
            "INSERT INTO users(id,username,display_name,password_hash) VALUES(?,?,?,?)",
            (user_id, name, display_name, hashed),
        )
    return user_id


def reset_password(database, name, password):
    hashed = password_hash(password)
    with database.transaction() as db:
        row = db.execute(
            "SELECT id FROM users WHERE username=?", (username(name),)
        ).fetchone()
        if not row:
            raise ValueError("Unknown user")
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, row["id"]))
        db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))


def login(db, name, password, address):
    current = time.time()
    try:
        normalized = username(name)
    except ValueError:
        normalized = "invalid"
    buckets = [
        ("user:" + sha(normalized.encode()), 8),
        ("ip:" + sha(address.encode()), 40),
    ]
    for bucket, maximum in buckets:
        row = db.execute(
            "SELECT * FROM login_attempts WHERE bucket=?", (bucket,)
        ).fetchone()
        if row and current - row["window_start"] < 300 and row["attempts"] >= maximum:
            return None, 429
    for bucket, _ in buckets:
        db.execute(
            "INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET attempts=CASE WHEN ?-window_start>=300 THEN 1 ELSE attempts+1 END, window_start=CASE WHEN ?-window_start>=300 THEN ? ELSE window_start END",
            (bucket, current, current, current, current),
        )
    user = db.execute(
        "SELECT * FROM users WHERE username=? AND active=1", (normalized,)
    ).fetchone()
    valid = verify_password(password, user["password_hash"] if user else DUMMY_HASH)
    if not user or not valid:
        return None, 401
    db.execute("DELETE FROM login_attempts WHERE bucket=?", (buckets[0][0],))
    db.execute("DELETE FROM sessions WHERE expires<?", (current,))
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions VALUES(?,?,?,?)",
        (sha(token.encode()), user["id"], csrf, current + 43200),
    )
    return {
        "token": token,
        "csrf": csrf,
        "user": {k: user[k] for k in ("id", "username", "display_name")},
    }, 200


def authenticate(db, token):
    if not token:
        raise HTTPException(401, "Authentication required")
    row = db.execute(
        "SELECT u.id,u.username,u.display_name,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.active=1",
        (sha(token.encode()), time.time()),
    ).fetchone()
    if not row:
        raise HTTPException(401, "Authentication required")
    return dict(row)


def lab_access(db, user_id, lab_id, admin=False):
    row = db.execute(
        "SELECT role FROM lab_members WHERE lab_id=? AND user_id=?", (lab_id, user_id)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Laboratory not found")
    if admin and row["role"] not in {"owner", "admin"}:
        raise HTTPException(403, "Laboratory administrator required")
    return row["role"]


def project_access(db, user_id, project_id, needed="viewer"):
    row = db.execute(
        "SELECT p.*,lm.role AS lab_role,pm.role AS project_role FROM projects p JOIN lab_members lm ON lm.lab_id=p.lab_id AND lm.user_id=? LEFT JOIN project_members pm ON pm.project_id=p.id AND pm.user_id=? WHERE p.id=?",
        (user_id, user_id, project_id),
    ).fetchone()
    if not row or (
        row["lab_role"] not in {"owner", "admin"} and row["project_role"] is None
    ):
        raise HTTPException(404, "Project not found")
    role = "admin" if row["lab_role"] in {"owner", "admin"} else row["project_role"]
    if {"viewer": 0, "editor": 1, "admin": 2}[role] < {
        "viewer": 0,
        "editor": 1,
        "admin": 2,
    }[needed]:
        raise HTTPException(403, "Insufficient project permission")
    return dict(row, effective_role=role)


def visible_projects(db, user_id):
    return [
        dict(r)
        for r in db.execute(
            "SELECT p.*, CASE WHEN lm.role IN ('owner','admin') THEN 'admin' ELSE pm.role END AS effective_role FROM projects p JOIN lab_members lm ON lm.lab_id=p.lab_id AND lm.user_id=? LEFT JOIN project_members pm ON pm.project_id=p.id AND pm.user_id=? WHERE lm.role IN ('owner','admin') OR pm.role IS NOT NULL ORDER BY p.name,p.id",
            (user_id, user_id),
        )
    ]
