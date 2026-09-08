# Integration contracts

All exchange is explicit and local. Exported copies do not inherit live laboratory permissions. The recipient must protect their copy independently. The application has no background connections to ChemData Auditor, Scientific Evidence Engine, notebook vendors, or AI providers.

## Project export 1.0

A ZIP export contains:

| Member | Meaning |
| --- | --- |
| `manifest.json` | Kind `internal_experimental_evidence`, schema version, laboratory/project scope, export actor/time, member SHA-256 hashes, quantity index, and interpretation limits |
| `records.json` | Active records with source references, units, uncertainty, author IDs, complete revision history, comments, links, and attachment metadata |
| `experiments.csv` | Record/version identity, project, attempt group, title, outcome, performed timestamp, source reference, author, and a restricted failure indicator |
| `auditor-config.json` | Configuration for the experiment table |
| `quantities/<hash>.csv` | One conditions/measurements name per table; value, unit, uncertainty, record ID, attempt group, source reference |
| `quantities/<hash>.audit.json` | Numeric, uncertainty, provenance, grouping, and unit checks for that table |
| `attachments/<uuid>.bin` | Optional original attachment bytes; original names and hashes remain in record metadata |

The failure indicator is `1` for failed, `0` for succeeded, and blank for partial/inconclusive. It is a convenience field, not a scientifically validated ML target. Measurements and uncertainty remain in their original units; uncertainty is interpreted in the same unit as its value. Conditions and measurements are separated, and quantity names are case-sensitive in the export index. Auditor configurations use the first encountered unit for each quantity table as the expected unit; compatible units can be converted by Auditor and incompatible dimensions should be investigated.

An attempt group is the lexicographically smallest record ID in the undirected connected component of recorded relationships, including archived intermediate nodes. Archived records themselves are omitted from exports, so group IDs or link targets can refer to archived records absent from `records.json`. Archiving does not establish independence between remaining attempts. The project database retains the complete relationship evidence. Missing links, shared batches, shared instruments, publication sources, and other dependence must still be assessed before choosing a split.

`failure-memory verify-export FILE.zip` rejects duplicate ZIP paths, unsafe paths, unsupported schemas, size/inventory/hash mismatches, inconsistent current revisions, attachment mismatches, and experiment/quantity tables that disagree with the record evidence. It does **not** authenticate the exporter or cryptographically sign a scientific claim. A party capable of rewriting all evidence and hashes can create a different internally consistent bundle. Review source references and obtain bundles through a trusted channel.

CSV is intended for machine ingestion. User-supplied text is preserved verbatim and can begin with spreadsheet formula characters. Import text columns explicitly as text if opening a CSV in spreadsheet software; do not enable formula execution on an untrusted export. This preserves original scientific strings without silently changing evidence.

## ChemData Auditor

Install ChemData Auditor separately. Integration tests were run against commit `eff3ed3c43ec71e9ceecabc1f04d1dfb5c91c116` from `yukevindai/chemdata-auditor` (0.3 package).

After verifying the bundle, use the official Python API directly without extracting ZIP paths:

```python
import io
import json
import zipfile
import pandas as pd
from chemdata_auditor import AuditConfig, audit
from failure_memory.exports import verify_bundle

raw = open('experiment-memory.zip', 'rb').read()
manifest, records = verify_bundle(raw)
with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
    data = pd.read_csv(io.BytesIO(bundle.read('experiments.csv')))
    config = AuditConfig(**json.loads(bundle.read('auditor-config.json')))
    report = audit(data, config)
```

Repeat using each quantity entry’s `csv` and `auditor_config` paths. Blank uncertainty values are intentionally retained so quality checks can surface missing uncertainty. Audit results can contain warnings on legitimate but incomplete research records; accepting a configuration does not imply the dataset is clean or suitable for ML. Use `attempt_group` as a minimum grouping boundary, then apply domain-appropriate laboratory, material-batch, composition, or temporal constraints in SciSplit. There are no official ML partitions in this application.

## Generic ELN import

`POST /api/projects/{project_id}/import` accepts one record with this envelope. This example is entirely synthetic:

```json
{
  "schema_version": "1.0",
  "connector": "generic_eln",
  "external_id": "SYNTHETIC-record-01",
  "record": {
    "title": "Synthetic electrolyte precipitation",
    "performed_at": "2026-01-01T12:00:00Z",
    "status": "failed",
    "materials": [{"name": "Synthetic salt", "lot": "DEMO-01"}],
    "conditions": [{"name": "temperature", "value": 25, "unit": "degC", "uncertainty": 0.5}],
    "procedure": ["Mix the synthetic demonstration materials"],
    "equipment": [{"name": "Demonstration balance", "asset_id": "DEMO"}],
    "outcomes": "Synthetic precipitate observed",
    "suspected_causes": [{"cause": "Water contamination", "confidence": "low", "rationale": "Water content was not measured"}],
    "fixes": [{"action": "Dry the materials", "outcome": "untried", "evidence": "Proposed for a later attempt"}],
    "uncertainty_notes": "Demonstration data only; no experiment was performed",
    "source": {"kind": "eln", "reference": "SYNTHETIC-ELN/record-01"}
  }
}
```

The combination `(project_id, connector, external_id)` is unique. Replaying the same normalized payload returns the original record with `replayed: true`; changing a previously imported payload returns 409. An authorized editor must inspect and submit an explicit revision to accept an upstream change. The import event preserves the connector, external ID, and canonical payload hash. This is a stable exchange contract, not a live vendor connector. Operator/importer assertions about a source are not independently verified.

Pydantic models in `src/failure_memory/models.py` define the full schema. Unknown fields, unrecognized units, negative uncertainty, non-finite values, boolean/numeric-string measurements, missing timezone, and duplicate quantity names are rejected. Physical plausibility beyond these generic rules belongs in ChemData Auditor and domain constraints.

## Scientific Evidence Engine bridge

Scientific Evidence Engine 0.2 ingests PDFs. This application therefore uses an explicit adapter, rather than representing an experiment export as an already published paper.

```bash
python -m pip install -e '.[integration]'
# Install Scientific Evidence Engine separately into the same environment.
failure-memory import-evidence experiment-memory.zip --output private-rendered-evidence --store private-evidence-store
```

The adapter was tested against Scientific Evidence Engine commit `01ee04ca9006854dc4d72fec8f005e041e4e2c22`. Both destination directories must be new. The adapter:

1. Verifies the project bundle and retains its exact bytes and authoritative `records.json`.
2. Renders each record, including its history, as a deterministic PDF labeled **Internal experimental evidence — researcher supplied**. JSON Unicode escapes preserve non-ASCII source characters independently of PDF font coverage.
3. Calls the engine’s `ingest_paper` API with explicit internal-experiment metadata, source reference, record/version ID, author ID, and bundle digest.
4. Calls `index_paper` to create exact page-and-character passage evidence and saves a bridge manifest mapping records to paper and evidence IDs.

The engine’s internal `paper_id` namespace is an implementation identifier; the title and metadata identify these documents as internal experiments. No claims are proposed, supported, or verified automatically. Researchers must inspect the original source, uncertainty, and extracted passages before linking a scientific claim. The local evidence store may be searched with the engine’s own research tools.

Destination directories are private by default. Access controls do not carry over to the engine; use a separate OS-protected store for each authorized project. If a batch fails partway through, keep the checkpoint manifest to inspect what was imported, resolve the cause, and rerun into new destinations. No existing evidence collection is overwritten or merged.
