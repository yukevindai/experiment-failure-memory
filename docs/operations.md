# Operations and API

## Deployment boundary

Run under a dedicated OS account with a private data directory (mode 0700). The application creates new database files with mode 0600; existing parent directory permissions remain the operator’s responsibility. SQLite WAL and SHM files reside beside the database. Keep this directory and backups outside web roots and source control. Use encrypted storage if your laboratory requires it.

Place an HTTPS reverse proxy in front of the loopback service. Set `--origin` to exactly the browser-facing scheme, host, and port. Host validation rejects unexpected hosts; write requests reject conflicting origins and require a custom application header plus an authenticated CSRF token. Do not enable CORS or embed the application on another origin. `--insecure-local` works only with a loopback HTTP origin and loopback binding. The default secure-cookie mode expects TLS at the proxy.

Configure the proxy to cap request bodies at 16 MiB and enforce connection/header/body timeouts, especially against slow uploads. The application independently bounds streamed request bodies, JSON validation, attachments, and exports. Client IP forwarding is disabled; login throttling uses the peer IP plus a username bucket. Behind a proxy, the IP bucket is shared across users (40 attempts per five minutes); each username is limited to eight failed attempts per five minutes. Consider network admission controls for larger teams.

Session tokens are random, stored as hashes, and expire after 12 hours. Cookies are HttpOnly, Secure in shared deployment, and SameSite Strict. CSRF values are held in browser memory. Passwords use salted scrypt. There is no public signup, password email, public attachment URL, external analytics, or CDN dependency.

## Roles

| Role | Effective access |
| --- | --- |
| Laboratory owner | All laboratory projects; manages administrators and members; cannot be removed/demoted through the API |
| Laboratory administrator | All laboratory projects; creates projects; manages ordinary laboratory members |
| Laboratory member | Only projects with an explicit grant |
| Project administrator | Read/export/write project data and manage its project grants |
| Project editor | Read/export/write project data, attachments, comments, and relationships |
| Project viewer | Read project records/history/attachments and explicitly export data |

Project administrators can grant access only to existing members of the same laboratory. Laboratory removal deletes project grants. Demoting a laboratory administrator to member removes inherited access; any separately assigned project grants remain effective. Permissions are checked from current database state inside the same transaction as each operation. A previously downloaded copy cannot be recalled by revoking access.

Operator commands `create-user`, `reset-password`, `disable-user`, and `enable-user` require direct access to the database file. Provision passwords interactively, or use `--password-env NAME` with a secret injected by your operator environment. Do not commit passwords or pass literal passwords as command arguments. There is no web interface for global account administration.

## Backup and restore

Use `failure-memory --database PATH backup DESTINATION` for a live, consistent SQLite snapshot. Do not copy only the main file while WAL transactions are active. Backups include attachment bytes and all revisions. Keep them on protected storage with a retention policy appropriate to your laboratory.

Restore into a **new** destination with `failure-memory --database NEW_PATH restore BACKUP`. The command checks schema version, SQLite integrity, and foreign keys, copies using SQLite backup, and deletes restored sessions and throttle buckets. It refuses to overwrite existing files. Verify the restored project through a local instance before directing users to it. A database integrity check is not proof of scientific correctness or source authenticity.

Schema version 1 is initialized automatically. Unsupported schema versions fail explicitly; there are no implicit migrations or destructive resets. Application revisions and events are append-only through the API, but a trusted operator with SQLite access can alter them. History and event records are not cryptographically signed.

## API essentials

All API paths below begin with `/api`. Login requires `X-EFM-Request: 1`. Authenticated writes additionally require `X-CSRF-Token` from login or `GET /me`. Use a cookie jar to retain the session cookie. No bearer token API is provided in this release.

| Method and path | Purpose |
| --- | --- |
| `POST /login`, `POST /logout`, `GET /me` | Session lifecycle |
| `GET/POST /labs` | List accessible laboratories / create a laboratory |
| `GET/PUT /labs/{id}/members` | Administrator membership management |
| `POST /labs/{id}/projects`, `GET /projects` | Create / list accessible projects |
| `GET/PUT /projects/{id}/members` | Explicit project grants |
| `GET /search?q=&project_id=&status=&archived=false&limit=50` | Permission-filtered lexical search |
| `POST /projects/{id}/records` | Create a structured experiment |
| `GET/PUT /records/{id}` | Full detail / optimistic revision |
| `POST /records/{id}/archive` | Archive or restore with expected version |
| `POST /records/{id}/comments` | Append a comment |
| `POST /records/{id}/links` | Add `repeat_of`, `fix_for`, or `derived_from` link |
| `GET /records/{id}/similar` | Ranked same-project comparisons |
| `POST /records/{id}/attachments?filename=…` | Upload raw bytes, at most 16 MiB |
| `GET /attachments/{id}` | Permission-checked, integrity-checked download |
| `GET /projects/{id}/patterns` | Cause and recovery evidence summaries |
| `GET /projects/{id}/events` | Last 500 project events, administrators only |
| `POST /projects/{id}/export?attachments=false` | Download project ZIP |
| `POST /projects/{id}/import` | Idempotent ELN import |

Record revisions accept `{"expected_version": 1, "record": {...}}`. Archive requests accept `{"expected_version": 1, "archived": true}`. A stale version returns 409 and preserves the editor’s draft in the browser. Reload and reconcile it with the latest revision. Membership requests accept `{"username": "bob", "role": "viewer"}` (or `remove`); laboratory roles are `admin` or `member`. A relationship request accepts `{"target_id": "UUID", "relation": "repeat_of"}`. Links stay within a project and cannot create cycles.

Search matches case-insensitive whole terms across titles, summaries, outcomes, uncertainty, tags, procedures, material/equipment names, causes, and recovery actions. Any matching term is sufficient; score is matched query terms divided by query terms. Ties retain newest-update ordering. Similarity uses term-set Jaccard overlap; where compatible condition names match, score is 70% lexical and 30% mean inverse relative numeric distance after unit conversion. No model, embeddings, trained diagnosis, or causal inference is involved.

## Browser smoke test

With Node and Playwright available, install its Chromium browser (`npx playwright install chromium`). Start a separate disposable development database and provision a synthetic account. Then:

```bash
EFM_TEST_URL=http://127.0.0.1:8000 EFM_TEST_USER=demo EFM_TEST_PASSWORD='your-synthetic-test-password' node tests/browser-smoke.cjs
```

This test creates a laboratory, project, experiment, revision, comment, and export in that disposable database. It also checks mobile overflow and logs out. Never run it against a real laboratory database. Python tests create isolated temporary databases and require no network access.
