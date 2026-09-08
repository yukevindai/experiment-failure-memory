# Experiment Failure Memory

A private, self-hosted application for preserving unsuccessful experiments and the lessons behind them. Researchers can search previous attempts, inspect the evidence behind suspected causes, and find out which recovery strategies were reported to work.

## What you can do

- Record materials and lots, equipment, procedures, conditions and measurements with units and uncertainty, observed outcomes, suspected causes, attempted fixes, and source references.
- Organize private laboratories and projects with owner, administrator, editor, and viewer permissions. Provision accounts locally; there is no public registration.
- Search accessible experiments by terms and outcome. Compare related attempts using shared terms and unit-compatible conditions, with the matching evidence shown.
- Inspect recurring causes and recovery outcomes, retaining the underlying record IDs and observations. These are descriptive self-reports, not causal estimates.
- Link repeated attempts, recoveries, and derived experiments; add comments and attachments; inspect immutable application revisions and actor-stamped events. Conflicting edits require review instead of overwriting another researcher’s changes.
- Export project JSON, CSV datasets, quantity tables, ChemData Auditor configurations, and hash-checked provenance manifests. Original attachments are opt-in.
- Import records through an idempotent ELN JSON contract. Optionally transfer an explicitly exported project into a new local Scientific Evidence Engine store as labeled internal experimental evidence.

The application includes a browser interface, a JSON API, a unified Python package, and a server-operator CLI. SQLite stores both records and attachment bytes, so consistent backups include the full workspace.

## Run locally

Python 3.10 or newer is required. From this repository:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
failure-memory --database data/memory.sqlite init
failure-memory --database data/memory.sqlite create-user alice --display-name 'Alice Researcher'
failure-memory --database data/memory.sqlite serve --origin http://127.0.0.1:8000 --insecure-local
```

Open `http://127.0.0.1:8000`. The account command prompts for a password of at least 12 characters. Sign in, open **Access & exchange**, create a laboratory and project, then select the project and choose **Record an experiment**. All displayed experiment times use your browser’s local timezone; stored timestamps retain an explicit timezone.

Create additional accounts with the same CLI. A laboratory owner adds an existing account as a laboratory member, then assigns its project role. A project viewer can inspect and explicitly export that project; an editor can also record, revise, comment, attach files, and link attempts. Project administrators manage project access. Laboratory owners and administrators inherit access to every project in that laboratory.

For a shared deployment, use an HTTPS reverse proxy and pass its exact public origin. The server defaults to loopback binding and secure cookies:

```bash
failure-memory --database /srv/efm/private/memory.sqlite serve --origin https://memory.example.org
```

The CLI does not itself provision TLS. See [deployment and access controls](docs/operations.md) before a shared deployment.

## Backup and account recovery

```bash
failure-memory --database data/memory.sqlite backup backups/memory-2026-09-08.sqlite
failure-memory --database restored/memory.sqlite restore backups/memory-2026-09-08.sqlite
failure-memory --database data/memory.sqlite reset-password alice
failure-memory --database data/memory.sqlite disable-user alice
```

Backup and restore refuse to overwrite a destination. Restores validate database integrity and revoke sessions. Password resets and account disabling also revoke sessions. Keep backup copies private: they contain experimental data, attachments, and password hashes. See [operations](docs/operations.md) for details.

## Exchange with the scientific reliability toolkit

Download an export from **Access & exchange**, then verify it locally:

```bash
failure-memory verify-export experiment-memory.zip
```

The archive contains `experiments.csv`, `auditor-config.json`, complete record provenance in `records.json`, per-quantity CSV/configuration pairs, and `manifest.json`. Attempt groups preserve recorded relationships even through archived intermediate attempts. Missing relationships still prevent any guarantee of experimental independence.

[Integration contracts](docs/integrations.md) describe the ChemData Auditor workflow, ELN import schema, and optional Scientific Evidence Engine bridge. Neither companion application is required for everyday operation. No experiments are sent to a hosted AI service or external notebook provider.

## Development and validation

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
node --check src/failure_memory/static/app.js
```

Tests exercise project and laboratory isolation, live revocation, read-only roles, CSRF, login throttling, concurrent revisions, attachment integrity, relationship cycles, unit comparison, idempotent imports, export verification, backups, and operator account management. Optional integration tests run against the actual companion packages when installed. A Playwright browser smoke test is available at `tests/browser-smoke.cjs`; its separate development dependencies and invocation are documented in [operations](docs/operations.md).

## Scope and limitations

This release targets one host and small research teams. SQLite serializes short transactions; search is lexical and scans at most 10,000 records in the selected scope, returning up to 200. Attachments are limited to 16 MiB, JSON requests to 1 MiB, and exports to a 64 MiB expanded budget. Large projects require separate projects or an operational migration; there is no distributed search service.

Filesystem administrators remain trusted. The application does not provide encryption at rest, SSO/MFA, tamper-proof audit signatures, malware scanning, background ELN synchronization, or vendor-specific notebook connectors. Use protected disks, private backups, HTTPS, and an appropriately isolated host. Records are archived rather than deleted; this is not a retention-policy or compliance management product.

Similarity scores are heuristic, uncertainty is researcher-supplied, and recovery counts can reflect dependent attempts and selective reporting. The tool preserves evidence and its limitations; it does not validate a scientific claim automatically.

MIT licensed. See [LICENSE](LICENSE).
