# RfAnalyzer Implementation Master Plan

> **For agentic workers:** This is a **coordination plan**, not an executable TDD task list. It decomposes the spec-complete RfAnalyzer v1 design into six sequenced sub-projects, each of which gets its own dedicated implementation plan written via `superpowers:writing-plans`. Sub-plans contain the TDD checkbox steps; this document defines the boundaries, sequence, shared contracts, and exit criteria that keep the sub-plans coherent. Read this end-to-end before writing or executing any sub-plan.

**Goal:** Take RfAnalyzer from `Draft v3 — ready to implement` (spec, OpenAPI, JSON Schema, deployment-config schema, seed library, ADRs 0001–0003) to a running v1 service that satisfies the contract end-to-end: catalog + assets + Run lifecycle + 12-stage pipeline + seven propagation models + bundled link-type plugins + adaptive geo fidelity + five analysis ops + canonical / derivative artifacts + predicted-vs-observed + OPSEC + observability, deployable via Docker Compose with a vendored TypeScript client for argus-flight-center.

**Architecture:** Python 3.12 monorepo under `src/rfanalyzer/`, FastAPI + uvicorn for the API process, separate worker processes consuming `runs` from Postgres via `SELECT … FOR UPDATE SKIP LOCKED`, content-addressed asset store behind a `StorageProvider` interface, in-process plugin registry loaded via `importlib.metadata` entry points with a deployment-config allowlist gate. Six sub-projects layer bottom-up; each produces working, testable software; later layers depend only on stable interfaces frozen by earlier layers.

**Tech Stack:** Python 3.12 · FastAPI · pydantic v2 · uv · ruff · mypy --strict · pytest + pytest-asyncio + hypothesis + Schemathesis · structlog · OpenTelemetry · Postgres 16 (`postgis/postgis:16-3.4`) · argon2id (passlib or argon2-cffi) · rfc8785 (JCS canonicalization) · httpx + tenacity · numpy · scipy · rasterio · pyproj · shapely · xarray · netCDF4 · cffi (for ITM / P.528 / P.1812 native ports) · openapi-typescript + openapi-fetch (TS client generation) · Docker + Docker Compose · GitHub Actions.

---

## How to use this document

1. **Before writing a sub-plan:** read this master plan, then read the spec sections, ADRs, and seed assets it cites for that sub-project. Do not start a sub-plan whose **Depends on** list is incomplete — the contracts it builds against are not yet frozen.
2. **Writing a sub-plan:** invoke `superpowers:writing-plans` against the sub-project's *Scope* and *Exit criteria*. The sub-plan saves to `docs/superpowers/plans/YYYY-MM-DD-<sub-project-slug>.md`; update the **Sub-plan** field below with the path once it exists.
3. **Sub-plan execution:** each sub-plan offers the standard execution-handoff choice (subagent-driven / inline). The master plan does not execute itself — it is read, sub-plans are written, sub-plans execute.
4. **Cross-artifact sync stays canonical.** Every sub-plan that touches a concept with a machine-readable representation must follow the four-surface checklist in [README.md](../../../README.md#cross-artifact-sync--required-for-every-spec-change) and re-run `scripts/check-sync.py` before claiming done. The implementation produces a *fifth* surface (pydantic-emitted OpenAPI under `src/rfanalyzer/_generated/`) which CI diffs against the spec-derived OpenAPI; do not let it diverge.
5. **Frequent commits.** Each sub-plan ships in many small commits. The 12-cleanup-units-in-one-commit pattern was a one-time spec consolidation; for code, prefer commits that match a single TDD red→green→refactor cycle.

---

## Source-of-truth pointers

| Concern | Authority |
|---|---|
| Behavior contract | [Spec v3](../specs/2026-04-25-rf-site-planning-api-design.md) — §1–§8, Appendices A–E |
| HTTP contract | [OpenAPI 3.1](../specs/2026-04-25-rf-site-planning-api.openapi.yaml) — derived; emitted by pydantic in implementation; CI diffs the two |
| Op A–E request bodies | [JSON Schema 2020-12](../specs/2026-04-25-analysis-requests.schema.json) |
| Operator-tunable knobs | [Deployment-config schema](../specs/2026-04-25-deployment-config.schema.json) |
| Seed library | [`standard-profile-library.json`](../specs/seed/standard-profile-library.json) + [`antenna_patterns/`](../specs/seed/antenna_patterns/) |
| Test fixtures | [`scenarios/`](../specs/seed/scenarios/) (12 runnable Op A–E fixtures), [`test-vectors/`](../specs/seed/test-vectors/) (golden numerical + canonicalization) |
| Stack | [ADR-0001](../../adr/0001-stack.md) |
| Auth + Postgres image + redaction | [ADR-0002](../../adr/0002-argus-alignment-and-auth.md) |
| Propagation-model registry | [ADR-0003](../../adr/0003-propagation-model-registry.md) |
| AI working agreements | [CLAUDE.md](../../../CLAUDE.md) |

---

## Repository layout (frozen by sub-project 1)

```
RfAnalyzer/
├── pyproject.toml                       uv-managed; dependency set per ADR-0001
├── uv.lock
├── .python-version                      3.12
├── ruff.toml / mypy.ini / pytest.ini    tool config
├── docker/
│   ├── Dockerfile                       multi-stage; slim runtime layer
│   ├── docker-compose.yml               api + worker + postgis + minio
│   └── compose.override.example.yml
├── scripts/
│   ├── check-sync.py                    already exists; pre-commit + CI
│   ├── emit-openapi.py                  dumps pydantic-emitted OpenAPI to _generated/
│   └── diff-openapi.py                  diffs emitted vs spec-derived; CI gate
├── src/rfanalyzer/
│   ├── __init__.py
│   ├── _generated/
│   │   └── openapi.yaml                 emitted by emit-openapi.py; gitignored or committed (decide in sub-project 1)
│   ├── api/                             FastAPI routers; one router per resource family
│   │   ├── runs.py
│   │   ├── analyses.py                  /analyses/p2p, /analyses/area, /analyses/multi_link, /analyses/multi_tx, /analyses/voxel
│   │   ├── catalog.py                   sites, antennas, radio_profiles, equipment_profiles, aoi_packs, clutter_tables, operating_volumes, measurement_sets, comparisons, regulatory_profiles
│   │   ├── assets.py                    initiate / part / complete / refresh_part_urls
│   │   ├── webhooks.py                  webhook registration + challenge + delivery log
│   │   └── health.py                    /healthz /readyz
│   ├── auth/
│   │   ├── bearer.py                    Authorization: Bearer <api-key>
│   │   ├── argon2.py                    argon2id verify
│   │   ├── principal.py                 Principal model per spec §8.4
│   │   └── scopes.py                    per-operation scope checks
│   ├── catalog/
│   │   ├── entities/                    one module per first-class entity (10)
│   │   ├── refs.py                      {ref, owner, version} resolution; "latest" handling
│   │   ├── sharing.py                   share-within-tenant rules
│   │   └── seed_loader.py               first-boot bootstrap of standard-profile-library.json
│   ├── assets/
│   │   ├── store.py                     content-addressed lifecycle (refcount, GC)
│   │   ├── multipart.py                 initiate / part / complete / refresh
│   │   └── purposes.py                  asset_purpose enum + per-purpose validation
│   ├── runs/
│   │   ├── lifecycle.py                 state machine incl. RESUMING; sweepers
│   │   ├── inputs_resolved.py           snapshot + RFC 8785 canonicalization (rfc8785 lib)
│   │   ├── replay.py                    replay incl. force_replay_across_major
│   │   ├── idempotency.py               Idempotency-Key handling
│   │   └── worker.py                    SKIP-LOCKED claim loop; lease + lease_token; tile-write idempotence
│   ├── pipeline/
│   │   ├── runner.py                    orchestrates 12 stages; one OTel span per stage
│   │   ├── stage_01_validate.py
│   │   ├── stage_02_resolve_inputs.py
│   │   ├── stage_03_select_geo_layers.py
│   │   ├── stage_04_select_models.py
│   │   ├── stage_05_compute_pathloss.py
│   │   ├── stage_06_apply_clutter_and_building_loss.py
│   │   ├── stage_07_polarization.py
│   │   ├── stage_08_link_budget.py
│   │   ├── stage_09_aggregate.py
│   │   ├── stage_10_emit_canonicals.py
│   │   ├── stage_11_emit_derivatives.py
│   │   └── stage_12_finalize.py
│   ├── models/                          propagation models
│   │   ├── interface.py                 ModelInterface ABC + ModelCapabilities + PathLossResult
│   │   ├── registry.py                  entry-point loader; allowlist gate; collision detection
│   │   ├── auto_select.py               §4.4 strategy + frozen scenario table
│   │   ├── core/                        free-space, two-ray (non-removable, not entry-point loaded)
│   │   └── plugins/                     p526, p530, itm, p528, p1812 (each ships as its own entry point)
│   ├── link_types/
│   │   ├── interface.py                 LinkTypePluginInterface
│   │   ├── registry.py                  entry-point loader
│   │   └── plugins/                     generic, lora, lte, drone_c2, rtk, vhf_telemetry
│   ├── geo/
│   │   ├── tiers.py                     T0–T4 fidelity contract
│   │   ├── projections.py               LAEA selection rule; antimeridian + polar gates
│   │   ├── aoi_pack.py                  ingest + validation
│   │   └── byo.py                       BYO data validation per §5.6
│   ├── artifacts/
│   │   ├── canonicals.py                link_budget, path_profile, geotiff, voxel, stats, best_server_raster, fidelity_tier_raster, point_query, link-type semantic outputs
│   │   ├── derivatives.py               kmz, png_with_worldfile, geojson_contours, geotiff_stack, rendered_cross_section, voxel slices
│   │   ├── voxel_slice.py
│   │   └── rederive.py                  POST /v1/runs/{id}/artifacts:rederive
│   ├── measurements/
│   │   ├── ingest.py
│   │   └── pvo.py                       predicted-vs-observed
│   ├── opsec/
│   │   ├── classification.py            Appendix E.3
│   │   ├── polygons.py                  restricted_species_polygons; auto-classification
│   │   └── redaction.py                 location_redacted / restricted_species behavior
│   ├── storage/
│   │   ├── interface.py                 StorageProvider ABC
│   │   ├── filesystem.py
│   │   ├── s3.py
│   │   └── azure_blob.py
│   ├── db/
│   │   ├── engine.py                    asyncpg / SQLAlchemy 2.x + async
│   │   ├── models.py                    SQLAlchemy ORM
│   │   └── migrations/                  alembic; first migration creates extension postgis + tenant_api_keys + base tables
│   ├── observability/
│   │   ├── logging.py                   structlog + redaction processor (ADR-0002 §3)
│   │   ├── tracing.py                   OTel spans
│   │   └── metrics.py                   Prometheus exporters
│   ├── webhooks/
│   │   ├── registry.py                  registration + challenge
│   │   ├── delivery.py                  HMAC signing; allowlist for restricted_species
│   │   └── secrets.py                   24 h grace rotation
│   ├── config/
│   │   └── deployment.py                pydantic model emitted from deployment-config.schema.json
│   ├── plugins/                         (third-party plugin examples & docs only; v1 base-pack lives in models/ and link_types/)
│   └── main.py                          FastAPI app factory; uvicorn entry
├── tests/
│   ├── unit/                            mirrors src layout
│   ├── integration/                     real Postgres + MinIO via docker-compose service containers
│   ├── property/                        hypothesis property tests (Op A–E body shapes; canonicalization)
│   ├── fuzz/                            Schemathesis fuzz against emitted OpenAPI
│   ├── golden/                          re-runs of seed/test-vectors/golden-test-vectors.json against the live engine
│   └── conftest.py
└── .github/workflows/
    ├── spec-sync.yml                    already exists
    ├── lint.yml
    ├── typecheck.yml
    ├── unit.yml
    ├── integration.yml
    ├── fuzz.yml
    └── openapi-diff.yml                 fails on emitted-vs-spec divergence
```

This layout is **frozen by sub-project 1**. Sub-projects 2–6 may add files inside it but must not restructure it.

---

## Shared interfaces (must be frozen before later sub-projects start)

These contracts cross sub-project boundaries. Each is owned by exactly one sub-project; later sub-projects depend on the frozen version. Do not let a later sub-project mutate an earlier sub-project's owned interface — open a new contract revision instead.

### Owned by sub-project 1 — toolchain

- **`pyproject.toml` dependency set.** The exact pinned versions every later sub-project imports against.
- **OpenAPI emission diff contract.** `scripts/emit-openapi.py` and `scripts/diff-openapi.py` exit codes; CI gate behavior on divergence.

### Owned by sub-project 2 — auth + storage + observability

- **`StorageProvider` ABC** (`src/rfanalyzer/storage/interface.py`). Methods: `put_object(key, body, content_type, metadata)`, `get_object(key)`, `head_object(key)`, `delete_object(key)`, `presigned_url_put(key, ttl, content_type, content_length)`, `presigned_url_get(key, ttl)`, `initiate_multipart(key, content_type, metadata)`, `presign_part(upload_id, part_number, ttl)`, `complete_multipart(upload_id, parts)`, `abort_multipart(upload_id)`. Mirrors argus's `src/lib/storage.ts` shape.
- **`Principal`** (`src/rfanalyzer/auth/principal.py`). Fields: `tenant_id: UUID`, `key_id: UUID`, `scopes: frozenset[str]`, `rate_limit_class: str | None`, `storage_class: str | None`. Spec §8.4.
- **`tenant_api_keys` table schema.** Locked per ADR-0002 §2; later sub-projects reference rows by `key_id`.
- **structlog redaction processor.** Default key set per ADR-0002 §3; tunable via `logging.redaction_keys`.
- **OTel span naming convention.** `rfanalyzer.<subsystem>.<operation>`; one span per pipeline stage in sub-project 5.
- **`DeploymentConfig` pydantic model.** Mirrors [`2026-04-25-deployment-config.schema.json`](../specs/2026-04-25-deployment-config.schema.json) 1:1.

### Owned by sub-project 3 — catalog + assets

- **Entity pydantic models.** All 10 first-class entity types (Site, Antenna, RadioProfile, EquipmentProfile, AOIPack, ClutterTable, OperatingVolume, MeasurementSet, Comparison, RegulatoryProfile). These are the runtime projection of spec §3.2; OpenAPI emission diffs against the spec-derived OpenAPI.
- **`{ref, owner, version}` resolution rules.** `version: int | "latest"`; cross-key references rejected; resolution at SUBMITTED freezes the version.
- **`Asset` model + content-addressed lifecycle.** SHA-256 prefix; refcount-on-SUBMITTED; orphan-TTL clock starts only after refcount hits zero; `:refresh_part_urls` semantics.
- **`AOILayer` shape.** Per cleanup PR 1 (nested `layers: { dtm, dsm, clutter, buildings }`).

### Owned by sub-project 4 — Run lifecycle + worker

- **`Run` table schema** (the "Run record IS the job"): id, key_id, operation, link_type, status, status_reason, sensitivity_class, inputs_resolved, inputs_resolved_sha256, inputs_resolved_at, engine_version, engine_major, models_used (with plugin_major + plugin_version + license + provenance per ADR-0003 amendment 4), data_layer_versions, fidelity_tier_dominant / min / max / max_possible, comparison_ids, resume_count, worker_lease, lease_token, leased_at, created_at, terminal_at, error (RunError schema), warnings, etc.
- **State machine.** Spec §8.1 states + RESUMING.
- **`inputs_resolved` snapshot algorithm.** Inline every catalog reference at SUBMITTED; canonicalize with `rfc8785`; record `inputs_resolved_at`.
- **Worker claim contract.** `SELECT … FOR UPDATE SKIP LOCKED`; lease + lease_token; content-addressed tile keys with the lease token suffix; sweeper resets stale leases; `WORKER_LEASE_LOST` warning on resumption.

### Owned by sub-project 5 — pipeline + plugin registries

- **`ModelInterface` ABC + `ModelCapabilities` + `PathLossResult`.** Per spec §4.2 + ADR-0003 amendment 1.
- **`LinkTypePluginInterface`.** Per spec §4.6.
- **`link_budget` argument schema.** Frozen shape per cleanup PR 5 (frequency_mhz, tx_eirp_dbm, rx_sensitivity_dbm, total_pathloss_db, polarization_mismatch_db split into base_db + depolarization_db, fade_margin_db, cable_loss_tx_db, cable_loss_rx_db, link_margin_db, plus resolved Tx/Rx EquipmentProfile snapshots).
- **Per-stage interface** (`src/rfanalyzer/pipeline/stage_NN_*.py`). Each stage is one module with one entry function `run(ctx) -> ctx` and exactly one OTel span. Stages communicate via a typed `PipelineContext`.
- **Plugin allowlist gate.** `plugins.propagation_models.{allow_third_party, allowlist}` from deployment-config; entry points outside the allowlist are logged-and-skipped (not startup failures).

### Owned by sub-project 6 — geo + analysis ops + artifacts + PvO

- No interfaces consumed by earlier sub-projects.

---

## Sub-project sequence

Six sub-projects, executed in order. Each ends with a green CI run on `main` and the sub-project's exit criteria all met.

```
1. Toolchain & repo skeleton
       │
       ▼
2. Auth, Postgres, storage, observability
       │
       ▼
3. Catalog service & asset model
       │
       ▼
4. Run lifecycle, worker, reproducibility
       │
       ▼
5. Pipeline, propagation models, link-type plugins
       │
       ▼
6. Geo, analysis ops (A–E), artifacts, PvO, OPSEC
```

There is no parallelism in v1; every sub-project depends on a stable interface from the previous one. Within a sub-project, individual TDD tasks may parallelize per the sub-plan's discretion.

---

## Sub-project 1 — Toolchain & repo skeleton

**Scope.** Stand up the empty repo as a working Python project with green CI, Docker Compose dev stack, and the OpenAPI emission diff gate. No runtime behavior — this sub-project's deliverable is "an engineer can `uv sync && uv run pytest && docker compose up` and everything is green."

**Authority.** [ADR-0001](../../adr/0001-stack.md) action items 1–3, 5. [ADR-0002](../../adr/0002-argus-alignment-and-auth.md) action item 2 (compose pins `postgis/postgis:16-3.4` + boot migration enables PostGIS).

**Files.** Everything in the [Repository layout](#repository-layout-frozen-by-sub-project-1) section above except `src/rfanalyzer/<subsystem>/*.py` modules — those are stubs only, with `pass` bodies and module docstrings citing the spec sections they implement. CI workflows under `.github/workflows/` all exist and are wired to call out to ruff / mypy / pytest / Schemathesis.

**Exit criteria.**
- `uv sync` resolves; `uv.lock` committed.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/`, `uv run pytest` all green (the test suite contains one trivial test until later sub-projects).
- `docker compose -f docker/docker-compose.yml up -d` brings up `api`, `worker`, `postgis`, `minio`; `/healthz` returns 200; `/readyz` returns 200 once Postgres + MinIO are reachable.
- `python scripts/check-sync.py` exits 0.
- `python scripts/emit-openapi.py` runs and emits to `src/rfanalyzer/_generated/openapi.yaml`. (At this stage the emitted OpenAPI is near-empty; the diff gate is wired but only enforces "spec OpenAPI is structurally valid YAML" until sub-project 3 has shaped models.)
- GitHub Actions: `lint`, `typecheck`, `unit`, `integration` (a no-op until later), `fuzz` (a no-op until later), `openapi-diff`, and the existing `spec-sync` all run on PR.
- `docs/superpowers/plans/2026-04-29-rfanalyzer-implementation-master-plan.md` (this file) referenced from `README.md` in a new "Implementation" section that points at the master plan and the active sub-plan.

**Depends on.** None.

**Risks / unknowns.**
- argus uses `src/lib/storage.ts` as the storage shape; ensure the Python ABC methods round-trip the same operations even though TypeScript and Python have different async idioms. Decided once during sub-project 2.
- Whether `_generated/openapi.yaml` is gitignored or committed. **Recommendation:** committed, so PR diffs surface OpenAPI changes for review. Decided in sub-plan #1.

**Sub-plan.** Not yet written. To be written next; saved to `docs/superpowers/plans/<YYYY-MM-DD>-toolchain-repo-skeleton.md`.

---

## Sub-project 2 — Auth, Postgres, storage, observability

**Scope.** Cross-cutting infrastructure: bearer-token auth with argon2id verification; PostGIS migration creating the extension and the `tenant_api_keys` table; `StorageProvider` interface with filesystem + S3 + Azure Blob implementations; structlog with the explicit-key redaction processor; OpenTelemetry tracer + Prometheus metrics scaffolding; pydantic-typed `DeploymentConfig` loader.

**Authority.** [ADR-0001](../../adr/0001-stack.md) action item 4. [ADR-0002](../../adr/0002-argus-alignment-and-auth.md) action items 1, 3, 5. Spec §8.4, §8.6.

**Files (new).** `src/rfanalyzer/auth/{bearer,argon2,principal,scopes}.py`, `src/rfanalyzer/storage/{interface,filesystem,s3,azure_blob}.py`, `src/rfanalyzer/observability/{logging,tracing,metrics}.py`, `src/rfanalyzer/config/deployment.py`, `src/rfanalyzer/db/{engine,models}.py`, `src/rfanalyzer/db/migrations/versions/0001_initial.py` (creates `postgis` extension + `tenant_api_keys`), `tests/unit/auth/`, `tests/unit/storage/`, `tests/integration/auth_test.py`, `tests/integration/storage_test.py`.

**Exit criteria.**
- `tenant_api_keys` table created via alembic; argon2id verification round-trips a known key.
- `Authorization: Bearer <api-key>` middleware rejects missing / expired / revoked keys with the spec's standard error model (Appendix D codes).
- `/healthz` returns 200 unconditionally; `/readyz` checks Postgres + storage; both per spec §8.6.
- `StorageProvider` test suite passes against filesystem locally and against MinIO in integration; methods on the ABC match argus's `src/lib/storage.ts` capabilities.
- structlog emits JSON in prod mode; redaction processor scrubs every key in the ADR-0002 §3 set (case-insensitive, recurse 5, replaced with literal `[REDACTED]`); a hypothesis property test confirms no key in the set survives a log emission at any of the 5 levels.
- `DeploymentConfig` pydantic model loads `2026-04-25-deployment-config.schema.json`-conformant YAML/JSON; rejects unknown keys.
- One OTel span emitted per HTTP request, named `rfanalyzer.api.<route>`.

**Depends on.** Sub-project 1 (toolchain).

**Risks / unknowns.**
- argon2id parameters (`memory_kib=65536`, `iterations=3`, `parallelism=4` per ADR-0002) need calibrating to ~50 ms on Larry's target hardware; the sub-plan must include a calibration task.
- The deployment-config schema file uses both nested objects and `additionalProperties: false`; the pydantic model must round-trip without losing `default` values.

**Sub-plan.** Not yet written.

---

## Sub-project 3 — Catalog service & asset model

**Scope.** All 10 first-class entity types as pydantic models + SQLAlchemy ORM tables + CRUD endpoints + sharing/versioning/soft-delete. Content-addressed asset model with initiate / direct PUT / multipart / `:refresh_part_urls` / complete; reference-counted lifecycle with refcount-on-SUBMITTED hook (the actual hook fires from sub-project 4, but the API and the refcount column ship here). Seed-loader wiring that boot-loads `standard-profile-library.json` + bundled antenna patterns on first startup.

**Authority.** Spec §3 (all subsections), §3.5 (assets), Appendix E.6 (PATCH for sensitivity_class). README's seed-counts row. [ADR-0003](../../adr/0003-propagation-model-registry.md) amendment 4 partially (adds `license`, `provenance` columns to `models_used`; full population happens in sub-project 5).

**Files (new).** `src/rfanalyzer/catalog/{refs,sharing,seed_loader}.py`; `src/rfanalyzer/catalog/entities/<entity>.py` × 10; `src/rfanalyzer/assets/{store,multipart,purposes}.py`; `src/rfanalyzer/api/{catalog,assets,webhooks}.py`; ORM models for each entity in `src/rfanalyzer/db/models.py`; `db/migrations/versions/0002_catalog.py`; webhook registration + challenge flow (delivery happens in sub-project 4); `tests/unit/catalog/`, `tests/integration/catalog_test.py`, `tests/integration/asset_upload_test.py`, `tests/integration/seed_loader_test.py`.

**Exit criteria.**
- All 10 entity CRUD endpoints round-trip OpenAPI examples; emitted OpenAPI now diffs cleanly against the spec-derived OpenAPI for the entity component schemas.
- `share: shared` rules enforced; cross-tenant reads blocked with the spec's standard error.
- `version: int | "latest"` resolution returns the highest active version when `latest`; pinned versions are immutable.
- Asset upload (direct, < 50 MB) and multipart (≥ 50 MB at 16 MiB parts) both end-to-end against MinIO; idempotent re-upload short-circuits via SHA-256.
- `POST /v1/assets/{id}:refresh_part_urls` returns fresh presigned URLs only for un-completed parts.
- First boot of the catalog DB creates the seed library exactly once; second boot is a no-op (records keyed by `(owner, name)`).
- All 12 seed scenarios under `seed/scenarios/` validate against the JSON Schema **and** all referenced catalog entries resolve against the freshly loaded seed library.
- Webhook registration succeeds end-to-end against a local stub receiver including the registration-challenge round-trip; secret rotation issues a new secret with a 24 h grace period during which both validate.
- `PATCH /v1/runs/{id}` for `sensitivity_class` is wired (Run table doesn't exist yet — endpoint stubs against a placeholder Run model for now and is fully exercised in sub-project 4).

**Depends on.** Sub-project 2 (auth + storage + db engine).

**Risks / unknowns.**
- AOIPack `layers: { dtm, dsm, clutter, buildings }` shape is correct per cleanup PR 1; verify against the OpenAPI before generating ORM models. AOI Pack content is geo data — but the v1 entity stores layer asset_refs only; ingest happens in sub-project 6.
- Seed-loader idempotence: the boot run must be safe across process restarts mid-load. A startup-time advisory lock keyed by `seed:bootstrap` is the simplest answer (post-v1.0 leader election lands later per ADR-0002).

**Sub-plan.** Not yet written.

---

## Sub-project 4 — Run lifecycle, worker, reproducibility

**Scope.** Run record + state machine + SUBMITTED→RUNNING→COMPLETED/PARTIAL/FAILED/CANCELLED/EXPIRED/RESUMING transitions; SKIP-LOCKED worker process consuming SUBMITTED runs; `inputs_resolved` snapshot using `rfc8785` JCS canonicalization; replay endpoint with `force_replay_across_major` + `reclassify_on_replay`; idempotency-key handling; checkpoint/resume; cancellation; webhook delivery (HMAC signing + 5 min replay window + restricted-species allowlist). Empty pipeline body — runs claim, transition through states, emit a placeholder canonical artifact, and reach a terminal state. Real propagation work lands in sub-project 5.

**Authority.** Spec §3.3 (Run record), §8.1 (lifecycle, leases, sweepers), §8.3 (reproducibility), §2.3 (idempotency), §2.4 (webhook signing). Cleanup PR 6 (canonicalization, asset GC race, multipart refresh — multipart already shipped in sub-project 3 but the SUBMITTED-bump-to-refcount hook lives here). [ADR-0001](../../adr/0001-stack.md) `rfc8785` library pin.

**Files (new).** `src/rfanalyzer/runs/{lifecycle,inputs_resolved,replay,idempotency,worker}.py`; `src/rfanalyzer/api/runs.py`; `src/rfanalyzer/webhooks/{delivery,secrets}.py`; `src/rfanalyzer/pipeline/runner.py` with stub stages (each stage is a no-op `def run(ctx): return ctx`); `db/migrations/versions/0003_runs.py` (Run table + worker_leases + idempotency_keys + webhook_deliveries); `tests/unit/runs/`, `tests/integration/run_lifecycle_test.py`, `tests/integration/replay_test.py`, `tests/integration/canonicalization_vector_test.py`.

**Exit criteria.**
- All Run state transitions exercised in tests; cancellation observed within the 60 s upper bound.
- `inputs_resolved` snapshot is deterministic for byte-equal inputs; idempotent re-submission with the same `Idempotency-Key` returns the original Run; divergent body returns `422 IDEMPOTENCY_KEY_BODY_MISMATCH` regardless of state.
- `seed/test-vectors/canonicalization-vector.json` round-trips through `rfc8785`; the **placeholder `expected_sha256` is replaced by the computed hash** in this sub-project's first conformant run, and the updated vector committed (per ADR-0001 carry-forward note + user direction 2026-04-29).
- Replay produces a byte-equal `inputs_resolved_sha256` for unchanged engine major; cross-major replay rejected without `force_replay_across_major: true`; per-plugin major drift produces `MODEL_PLUGIN_MAJOR_DRIFT` / `LINK_TYPE_PLUGIN_MAJOR_DRIFT` warnings (plugin majors are placeholder-zero until sub-project 5).
- Worker claims a SUBMITTED run via SKIP LOCKED; lease + lease_token written; tile-write key suffix uses the lease token; sweeper resets stale leases (lease TTL configurable; default 60 s for the sub-project tests, 600 s in prod).
- Webhook delivery: HMAC-signed body byte-for-byte equal to what the receiver gets; `signed_at` within 5 min; restricted-species events go only to the configured allowlist URLs.
- Asset refcount bumps when a Run reaches SUBMITTED and decrements when a Run reaches a terminal state with referenced canonicals GC'd.
- `POST /v1/runs/{id}/resume` resumes from `RESUMING` and increments `resume_count`.
- Comparison auto-pin enforces `max_pinned_runs` and returns `409 PINNED_RUN_CAP_WOULD_BE_EXCEEDED` when exceeded.

**Depends on.** Sub-project 3 (catalog + assets).

**Risks / unknowns.**
- The `rfc8785` Python library's behavior under JS-double-to-string semantics for floats: confirm the documented trap (cleanup PR 6) with a hypothesis property test that round-trips floats sampled across the IEEE-754 representable range.
- Worker process supervision: in dev, run a single worker via `uvicorn`-adjacent process (`python -m rfanalyzer.runs.worker`); in prod, replicate. Sub-plan must include a documented supervision recipe.
- Webhook registration challenge: cleanup PR 7 added the flow but did not specify replay protection on the challenge itself. Decision: include a per-challenge nonce + 60 s TTL.

**Sub-plan.** Not yet written.

---

## Sub-project 5 — Pipeline, propagation models, link-type plugins

**Scope.** Real 12-stage pipeline body; full plugin registries (model + link-type); seven propagation models (free-space + two-ray core-bundled, P.526 / P.530 / ITM / P.528 / P.1812 as plugins); six link-type plugins (`generic` core, `lora` / `lte` / `drone_c2` / `rtk` / `vhf_telemetry` bundled). Auto-select strategy with the frozen `(operation, link_type, geometry) → scenario` table. Polarization mismatch (table + per-clutter-class depolarization).

**Authority.** Spec §4 (all subsections), §4.5 (polarization), Appendix B (band coverage). [ADR-0003](../../adr/0003-propagation-model-registry.md) (registry, license/provenance/runtime, core-bundled split, allowlist gate). Cleanup PR 5 (PathLossResult, link_budget shape, plugin lifecycle, scenario_suitability frozen set).

**Files (new).** `src/rfanalyzer/models/{interface,registry,auto_select}.py`; `src/rfanalyzer/models/core/{free_space,two_ray}.py`; `src/rfanalyzer/models/plugins/{p526,p530,itm,p528,p1812}/` (each as its own importable subpackage with its own entry point); `src/rfanalyzer/link_types/{interface,registry}.py`; `src/rfanalyzer/link_types/plugins/{generic,lora,lte,drone_c2,rtk,vhf_telemetry}/`; full bodies for `pipeline/stage_NN_*.py`; `tests/unit/models/<model>/`, `tests/unit/link_types/<plugin>/`, `tests/golden/golden_test_vectors_test.py` re-running every entry in `seed/test-vectors/golden-test-vectors.json` against the live engine, `tests/property/polarization_test.py`.

**Exit criteria.**
- Free-space and two-ray are core-bundled — present without entry-point loading; not subject to allowlist; not subject to plugin-major drift; not eligible for `MODEL_PLUGIN_CRASH` retry.
- All five plugin models register via Python entry points; each declares `id`, `name`, `license`, `provenance`, `runtime` per ADR-0003 amendment 1; missing `license` or `provenance` fails startup.
- Allowlist gate: an entry point not in `plugins.propagation_models.allowlist` is logged-and-skipped; a typo in the allowlist does not brick startup.
- Auto-select strategy walks the frozen `(operation, link_type, geometry) → scenario` table; falls back to free-space (T0) if no row matches.
- `PathLossResult` returned by every model; `components` populated where the model can decompose; `model_warnings` map to Appendix D codes.
- `link_budget` argument to `LinkTypePluginInterface.emit` matches the frozen schema (cleanup PR 5).
- Polarization mismatch table verified against the spec §4.5 base values; depolarization factor sourced from `ClutterTable.depolarization_factor_per_class`; the 3 dB floor in dense canopy explicit; matching polarization (base ≤ 3 dB) exempt.
- All entries in `golden-test-vectors.json` re-run against the live engine match within tolerance.
- `Run.models_used[]` populated with `license`, `provenance`, `plugin_major`, `plugin_version` per entry; replay drift rules from sub-project 4 now exercise real plugin majors.

**Depends on.** Sub-project 4 (Run lifecycle).

**Risks / unknowns.**
- **P.1812 port via crc-covlib** (Tier 3 per ADR-0003). Pure-Python port vs `cffi` wrap is a sub-plan decision. Either way, validation against published P.1812 reference outputs is the long pole. Sub-plan must include a validation-suite task.
- **ITM port via NTIA `its-propagation/itm`** (Tier 2). Likely `cffi` wrap; Windows + Linux build matrix needs verification.
- **P.528 lookup tables** (Annex 2): mass of static data; vendor as a seed asset alongside antenna patterns or embed in the wheel? Recommend embed, with a generation script committed.
- Plugin entry-point name collisions are startup-time fail-fast (per spec §4.2 / §4.6 "alphabetical by entry-point name unless `RFANALYZER_PLUGIN_ORDER` overrides; ID collision is a startup-time fail-fast"). Sub-plan must include a startup-collision integration test.

**Sub-plan.** Not yet written.

---

## Sub-project 6 — Geo, analysis ops (A–E), artifacts, PvO, OPSEC

**Scope.** Adaptive geo-data fidelity (T0–T4); AOI Pack ingest (DTM / DSM / clutter / buildings); BYO data validation; projection rules (LAEA selection, antimeridian rejection, polar projection warning, WGS84-only inputs); five analysis ops (P2P, area, multi-link, multi-Tx, voxel) with sync / async / auto promotion; canonical artifact emission (geotiff, voxel, link_budget, path_profile, stats, best_server_raster, fidelity_tier_raster, point_query, link-type semantic outputs); derivative emission (kmz, png_with_worldfile, geojson_contours, geotiff_stack, rendered_cross_section, voxel slices); voxel slicing endpoint; `:rederive`; predicted-vs-observed reporting; OPSEC classification + auto-classification + per-class redaction + restricted-species webhook allowlist.

**Authority.** Spec §2.3 (sync/async/auto), §4.0 (Tx/Rx, frequency authority, per-Op pairing), §5 (geospatial), §6 (artifacts), §7 (measurements + PvO), Appendix A (Op×Output matrix), Appendix E (OPSEC). Cleanup PR 8 (coordinate / projection / antimeridian / polar / datum), PR 4 (Op A outputs widening, Op E shape), PR 11 (canonical-vs-derivative drift).

**Files (new).** `src/rfanalyzer/geo/{tiers,projections,aoi_pack,byo}.py`; `src/rfanalyzer/api/analyses.py`; `src/rfanalyzer/artifacts/{canonicals,derivatives,voxel_slice,rederive}.py`; `src/rfanalyzer/measurements/{ingest,pvo}.py`; `src/rfanalyzer/opsec/{classification,polygons,redaction}.py`; `tests/integration/op_<a..e>_test.py` re-running each scenario from `seed/scenarios/` end-to-end; `tests/integration/opsec_test.py`; `tests/integration/pvo_test.py`; Schemathesis fuzz now runs against the full live API.

**Exit criteria.**
- All 12 seed scenarios (`seed/scenarios/*.json`) execute end-to-end against the running service and reach a terminal state matching the scenario's expected outcome (where the scenario specifies one). `restricted_species` scenarios return 404 to keys without `opsec.read_restricted_species`.
- Adaptive fidelity reports four tier values per Run (`dominant`, `min`, `max`, `max_possible`); a Run completes as `PARTIAL` rather than `COMPLETED` when fidelity is below the AOI's max possible.
- Antimeridian crossings rejected with `BBOX_CROSSES_ANTIMERIDIAN_NOT_SUPPORTED`; polar AOIs (north > 85, south < −85) processed in EPSG:3413 / EPSG:3031 with `POLAR_PROJECTION_DEGRADED`; non-WGS84 inputs rejected with `UNSUPPORTED_CRS`.
- LAEA centroid selection: EPSG:3035 for EU-wide AOIs, EPSG:9311 for North America, computed-LAEA for elsewhere.
- Sync responses bounded by `sync_budget_seconds` (default 25 s); auto-mode promotes to async with 202 hand-off when the budget would be exceeded.
- Canonical artifacts persist to per-class TTLs; derivatives regenerate from canonicals and cache 24 h; `POST /v1/runs/{id}/artifacts:rederive` produces alternate-style artifacts without re-running propagation.
- Voxel slicing returns subsets in the requested format (`geotiff`, `geotiff_stack`, `voxel_subset`, `json_point_grid`).
- Predicted-vs-observed: filter rules dimensionally coherent (frequency tolerance defaults to half the radio's bandwidth; metric coherence enforced); aggregates (`mean`, `median`, `rmse`, `max_abs`, `bias_direction`, per-clutter-class breakdown) match the spec's documented shape; `FilterReport` returned for filtered observations.
- OPSEC auto-classification: a Run whose geometry intersects a configured `restricted_species_polygons` polygon receives `sensitivity_class: restricted_species` with `OPSEC_AUTO_CLASSIFIED` warning; webhook delivery for restricted-species events restricted to the allowlist URL set.
- Schemathesis fuzz against the live API (under the emitted OpenAPI) green; OpenAPI-diff CI gate green.
- TS client generated from the emitted OpenAPI via `openapi-typescript` + `openapi-fetch`; the generated source committed at the path argus-flight-center expects (per ADR-0001 action item 6 as revised 2026-04-29: vendored, not published).

**Depends on.** Sub-project 5 (pipeline + plugins).

**Risks / unknowns.**
- **`restricted_species_polygons`** are deployment-config (never committed). The integration tests need a fixture polygon set generated at test setup; document the convention in the seed README.
- **AOI Pack DSM ingest** for buildings (Tier T4) is heavy; sub-plan should scope T4 to a smoke test (one small AOI) and cover T0–T3 fully.
- **License gate before public release.** `LICENSE` file not yet committed; before the v1 tag, add `LICENSE` (Apache-2.0 per README), `SECURITY.md` covering `restricted_species_polygons` hygiene, and a `CONTRIBUTING.md` referencing the four-surface sync rule.

**Sub-plan.** Not yet written.

---

## Cross-cutting concerns (all sub-projects)

These rules apply across every sub-plan; do not let a sub-plan ignore them.

1. **Spec-first stays canonical.** If a pydantic model and the spec disagree, fix the model. The only exception is when the spec itself has a typo — in that case, fix the spec in the same commit and re-run `scripts/check-sync.py`.
2. **One concept = four-surface sync.** Any change to a catalog entity, error/warning/filter code, enum value, or pipeline stage propagates across spec markdown + OpenAPI + JSON Schema + seed in the same commit. The fifth surface (pydantic-emitted OpenAPI under `_generated/`) is the implementation projection; CI diffs it against the spec-derived OpenAPI.
3. **TDD red→green→refactor.** Every sub-plan's tasks follow the writing-plans skill's bite-sized step pattern (write failing test → run it to confirm it fails → implement minimum → run it to confirm it passes → commit).
4. **Frequent commits.** Sub-plans aim for ~one commit per task; do not batch a sub-project into one giant PR.
5. **No backwards-compatibility shims yet.** v1 has no deployed users to break; if a sub-plan finds a flaw in an earlier sub-plan's interface, fix the interface and re-run the affected sub-plan's tests rather than layering compatibility code.
6. **Sub-plan handoff** at the end of each sub-project: announce green CI on `main`; the next sub-plan begins.

---

## Open questions resolved 2026-04-29

| # | Question | Resolution |
|---|---|---|
| 1 | TS-client publishing target (registry / package) | **Removed.** Generate the client and vendor the source directly into argus-flight-center; no registry, no package publication. ADR-0001 row 44 + action item 6 updated in this same change. |
| 2 | Canonicalization-vector placeholder hash | Computed and committed by sub-project 4's first conformant run (per ADR-0001 carry-forward note). |

---

## Open questions still standing

| # | Question | Owner | Resolution due |
|---|---|---|---|
| 3 | Commit `_generated/openapi.yaml` to the repo, or gitignore it? | Sub-plan #1 | Before sub-project 1 ships |
| 4 | P.1812 / P.528 / ITM: pure-Python port vs `cffi` wrap | Sub-plan #5 | Before sub-project 5 ships |
| 5 | Worker supervision recipe for prod (systemd / Docker restart / k8s Deployment) | Sub-plan #4 | Before sub-project 4 ships |
| 6 | Plugin sandboxing ADR (deferred per ADR-0001 / ADR-0003) | New ADR | Before onboarding any third-party plugin (post-v1.0) |
| 7 | Pre-public-release artifacts: `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md` | Sub-plan #6 | Before v1 git tag |

---

## Self-review

Spec coverage check — every spec section has a sub-project home:

| Spec section | Sub-project |
|---|---|
| §1 Purpose & Scope | (no implementation work; informational) |
| §2.1 Service topology | 1 (compose) |
| §2.2 Deployment shape | 1 (compose) |
| §2.3 API contract shape (sync/async/auto, idempotency) | 4 (idempotency); 6 (sync/async/auto promotion) |
| §2.4 Webhooks | 4 (delivery); 3 (registration) |
| §2.5 Endpoint inventory | 3 (catalog + assets); 4 (runs); 6 (analyses) |
| §3.1 Identity, sharing, versioning | 3 |
| §3.2 First-class entities (10) | 3 |
| §3.3 Run record | 4 |
| §3.4 Standard profile library | 3 (seed loader) |
| §3.5 Assets | 3 (lifecycle); 4 (refcount-on-SUBMITTED) |
| §3.6 Reference graph | 3 |
| §3.7 Regulatory Profile semantics | 3 |
| §4.0 Tx/Rx, frequency authority, pairing | 6 (validation); 5 (use during pipeline) |
| §4.1 12-stage canonical pipeline | 4 (stub stages); 5 (real bodies) |
| §4.2 Model plugin contract | 5 |
| §4.3 Models supported | 5 |
| §4.4 Auto-select strategy | 5 |
| §4.5 Polarization mismatch | 5 |
| §4.6 Link-type plugin contract | 5 |
| §5.1 Layer types | 6 |
| §5.2 Bundled global baseline | 6 (seed) |
| §5.3 AOI Pack lifecycle | 3 (entity); 6 (ingest) |
| §5.4 Adaptive fidelity contract | 6 |
| §5.5 Coordinate systems & projections | 6 |
| §5.6 BYO data validation | 6 |
| §6.1 Universal artifacts | 6 |
| §6.2 Link-type semantic outputs | 5 (definition); 6 (emission) |
| §6.3 Color mapping | 6 |
| §6.4 Coordinate-resolved point queries | 6 |
| §6.5 Multi-link Op C aggregation | 6 |
| §6.6 Voxel slicing | 6 |
| §6.7 Re-deriving outputs | 6 |
| §7 Measurements + PvO | 6 |
| §8.1 Run lifecycle, leases, sweepers | 4 |
| §8.2 Retention | 4 (canonicals); 6 (derivatives) |
| §8.3 Reproducibility & replay | 4 |
| §8.4 Auth & rate limiting | 2 |
| §8.5 Local-mode constraints | 1 (compose); 6 (asset-URL proxying) |
| §8.6 Observability | 2 |
| §8.7 Engine version & change management | 4 (engine_version recording); 5 (plugin majors) |
| §8.8 Performance characterization | 6 (post-v1; benchmark harness in tests/) |
| §8.9 Large-data transport | 3 (multipart); 6 (voxel slicing); 4 (multipart refresh) |
| Appendix A Op×Output matrix | 6 |
| Appendix B Band coverage | 5 |
| Appendix C Definitions | (informational) |
| Appendix D Errors / warnings / filter reasons | every sub-project (each adds the codes it raises) |
| Appendix E OPSEC | 6 (classification + redaction); 3 (PATCH); 4 (replay reclassification) |

No spec section is unowned. ADR-0001 / 0002 / 0003 action items all map to sub-projects 1–6.

---

## Execution handoff

This master plan does not execute itself. The next step is to write **sub-plan #1 (toolchain & repo skeleton)** via `superpowers:writing-plans`. Once that sub-plan is saved, it offers the standard subagent-driven vs inline execution choice.

**Recommendation:** subagent-driven for sub-plans 1, 2, 3 (mostly mechanical scaffolding), inline for sub-plans 4, 5, 6 (the model and pipeline work benefits from continuity in a single session because the design decisions cross many task boundaries).

When ready, run `/superpowers:writing-plans` with the scope:

> Write the implementation plan for sub-project 1 (toolchain & repo skeleton) per `docs/superpowers/plans/2026-04-29-rfanalyzer-implementation-master-plan.md`. Authority: ADR-0001 action items 1–3, 5; ADR-0002 action item 2 (compose pin). Exit criteria as listed in the master plan's "Sub-project 1" section.
