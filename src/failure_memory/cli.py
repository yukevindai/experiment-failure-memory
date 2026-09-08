"""Server-operator commands. Account administration requires database filesystem access."""

import argparse
import getpass
import json
import os
import sqlite3
from pathlib import Path

from .auth import create_user, reset_password, username
from .db import Database
from .exports import verify_bundle


def restore(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError("Backup does not exist")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup integrity check failed")
        if db.execute("SELECT version FROM schema_version").fetchall() != [(1,)]:
            raise ValueError("Unsupported backup schema")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Backup foreign key check failed")
    Database(source).backup(destination)
    with sqlite3.connect(destination) as db:
        db.execute("DELETE FROM sessions")
        db.execute("DELETE FROM login_attempts")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Experiment Failure Memory")
    parser.add_argument("--database", default="data/memory.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    for name in ("create-user", "reset-password", "disable-user", "enable-user"):
        command = sub.add_parser(name)
        command.add_argument("username")
        if name == "create-user":
            command.add_argument("--display-name", required=True)
        if name in ("create-user", "reset-password"):
            command.add_argument(
                "--password-env",
                help="Read password from this environment variable instead of prompting",
            )
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--origin", default="https://localhost:8000")
    serve.add_argument("--insecure-local", action="store_true")
    sub.add_parser("backup").add_argument("destination")
    sub.add_parser("restore").add_argument("source")
    sub.add_parser("verify-export").add_argument("bundle")
    evidence = sub.add_parser("import-evidence")
    evidence.add_argument("bundle")
    evidence.add_argument(
        "--output",
        required=True,
        help="New private directory for rendered internal evidence",
    )
    evidence.add_argument(
        "--store", required=True, help="Local Scientific Evidence Engine store"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "restore":
            restore(args.source, args.database)
        elif args.command == "verify-export":
            manifest, _ = verify_bundle(Path(args.bundle).read_bytes())
            print(json.dumps(manifest, indent=2))
        elif args.command == "import-evidence":
            from .evidence import import_evidence

            print(
                json.dumps(
                    import_evidence(args.bundle, args.output, args.store), indent=2
                )
            )
        elif args.command == "serve":
            import uvicorn

            from .app import create_app

            if args.insecure_local and args.host not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }:
                raise ValueError("Insecure development must bind to loopback")
            app = create_app(args.database, args.origin, not args.insecure_local)
            uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False)
        else:
            database = Database(args.database)
            if args.command != "init" and not database.path.exists():
                raise ValueError("Initialize the database first with init")
            database.initialize()
            if args.command == "backup":
                database.backup(args.destination)
            elif args.command in {"create-user", "reset-password"}:
                password = (
                    os.environ[args.password_env]
                    if args.password_env
                    else getpass.getpass("Password (12+ characters): ")
                )
                if args.command == "create-user":
                    create_user(database, args.username, args.display_name, password)
                else:
                    reset_password(database, args.username, password)
            elif args.command != "init":
                with database.transaction() as db:
                    target = db.execute(
                        "SELECT id FROM users WHERE username=?",
                        (username(args.username),),
                    ).fetchone()
                    if not target:
                        raise ValueError("Account not found")
                    db.execute(
                        "UPDATE users SET active=? WHERE id=?",
                        (int(args.command == "enable-user"), target["id"]),
                    )
                    db.execute("DELETE FROM sessions WHERE user_id=?", (target["id"],))
            print("Completed: " + args.command)
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
