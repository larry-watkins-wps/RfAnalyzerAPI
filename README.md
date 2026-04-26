# RfAnalyzer

> Self-hosted, single-tenant API for RF propagation analysis. Targets field engineers placing **autonomous drone docks**, **sub-GHz IoT endpoints** (camera traps, fence/gate sensors, animal collars), **LoRa gateways**, **GNSS RTK base stations**, and **LTE backhaul** in remote/protected areas. Wildlife-protection deployments are the primary v1 driver; no spec primitive is wildlife- or vendor-specific.

**Status:** Draft v3 — ready to implement. No implementation code yet, but the design contract has retired every BLOCKER/HIGH finding from the 2026-04-25 audit (see [`docs/cleanup-plan.md`](docs/cleanup-plan.md)) and the stack is locked in [ADR-0001](docs/adr/0001-stack.md). This README is a guided tour of what's been specified.

---

## Repository contents

```
RfAnalyzer/
├── README.md                                          ← you are here
├── CLAUDE.md                                          AI-assistant working agreements
├── .gitignore
└── docs/superpowers/specs/
    ├── 2026-04-25-rf-site-planning-api-design.md     Spec v2 — source of truth (~1500 lines, 9 mermaid diagrams)
    ├── 2026-04-25-analysis-requests.schema.json      JSON Schema 2020-12 for Op A–E request bodies
    ├── 2026-04-25-rf-site-planning-api.openapi.yaml  OpenAPI 3.1 — every endpoint + every entity schema
    ├── examples/
    │   ├── README.md                                  Index of examples
    │   ├── op-a-p2p.md                                Sync P2P with full link budget
    │   ├── op-b-area.md                               Async area heatmap with webhook delivery
    │   ├── op-c-multi-link.md                         Multi-link site report (PARTIAL run case)
    │   ├── op-d-multi-tx.md                           Multi-Tx best-server with NoData & tiebreak
    │   ├── op-e-voxel.md                              3D / volumetric coverage with voxel slicing
    │   └── asset-upload.md                            Direct + multipart asset upload
    └── seed/
        ├── README.md                                  Boot sequence + coverage notes
        ├── standard-profile-library.json              System-owned catalog seed (21 antennas, 18 radio profiles, 23 equipment profiles, 2 clutter tables)
        ├── antenna_patterns/                          Bundled MSI Planet pattern files referenced by sha256
        └── generate_patterns.py                       Reproduction script for the pattern files
```

## What the API does

Five analysis operations, all flowing through one pluggable model registry + 12-stage pipeline (spec §4.1). Every op accepts inline-or-reference catalog entities and produces caller-selected outputs.

| Op | Endpoint | Question it answers | Worked example |
|---|---|---|---|
| **A — point-to-point** | `POST /v1/analyses/p2p` | Will this specific link close? | [op-a-p2p.md](docs/superpowers/specs/examples/op-a-p2p.md) |
| **B — area heatmap** | `POST /v1/analyses/area` | What's coverage from this Tx across this AOI? | [op-b-area.md](docs/superpowers/specs/examples/op-b-area.md) |
| **C — multi-link site** | `POST /v1/analyses/multi_link` | Combined LoRa+LTE+drone-C2+RTK from one candidate site? | [op-c-multi-link.md](docs/superpowers/specs/examples/op-c-multi-link.md) |
| **D — multi-Tx best-server** | `POST /v1/analyses/multi_tx` | Of these candidate sites, which dominates per pixel? | [op-d-multi-tx.md](docs/superpowers/specs/examples/op-d-multi-tx.md) |
| **E — 3D / volumetric** | `POST /v1/analyses/voxel` | Coverage across a drone flight envelope (lat × lon × altitude)? | [op-e-voxel.md](docs/superpowers/specs/examples/op-e-voxel.md) |

## Design surface

### Pluggable propagation models (spec §4.2 – §4.4)
The engine ships seven plug-in models — **ITU-R P.1812**, **ITM/Longley-Rice**, **ITU-R P.528** (air-to-ground, used for Op E with drone C2), **ITU-R P.530**, **ITU-R P.526**, **free-space (Friis)**, and **two-ray ground reflection**. The auto-select strategy filters by frequency range, scores by `(operation, link_type, geometry) → scenario` suitability via the frozen mapping table in §4.4, and down-weights any model whose required data tier exceeds what the AOI provides. Callers may pin a specific model. Bundled link-type plugins: `lora`, `lte`, `drone_c2`, `rtk`, `vhf_telemetry`.

### Adaptive geo-data fidelity (spec §5.4)
Five tiers from `T0_FREE_SPACE` (sanity bound) to `T4_SURFACE_PLUS_BUILDINGS` (DSM + per-building loss). Each Run reports four tier values: `dominant`, `min`, `max`, and `max_possible` — the last is the best the AOI's data could support, regardless of what the run used. A run completes as `PARTIAL` rather than `COMPLETED` when fidelity is below the AOI's max possible (the engineer learns "I could have gotten more"). Callers may specify `min_fidelity_tier` (per-pixel floor) or `min_fidelity_coverage: {tier, fraction}` (coverage floor).

### Catalog with sharing and versioning (spec §3.1 – §3.2)
Ten first-class entity types — Site, Antenna, RadioProfile, EquipmentProfile, AOIPack, ClutterTable, OperatingVolume, MeasurementSet, Comparison, RegulatoryProfile — plus a content-addressed Asset model. Each entity is named, versioned, optionally shared within the tenant. References use `{ref, owner, version}` with `version: int | "latest"`; cross-key references are not supported.

A reference graph (mermaid ER diagram) lives in spec §3.6.

### Reproducible runs (spec §3.3, §8.3)
Every Run records a frozen `inputs_resolved` snapshot — every catalog reference fully inlined at the SUBMITTED transition (timestamp recorded as `inputs_resolved_at`). `engine_version`, `engine_major`, `models_used[]` (with plugin versions), and per-layer `data_layer_versions` are recorded; `POST /v1/runs/{id}/replay` reruns identically against the engine major recorded on the original. Cross-major replay requires explicit `force_replay_across_major: true`.

### Sync, async, and auto-promotion (spec §2.3)
Each analysis endpoint accepts `mode=sync|async|auto`. `auto` selects async for grid ops or large geometries (>250 k cells / >100 km²). Sync responses are bounded by `sync_budget_seconds` (default 25 s); on overrun the orchestrator auto-promotes to async with a 202 hand-off — the underlying Run continues running and reaches its terminal state normally. Sequence diagram in spec §2.3.

### Idempotency and webhook delivery (spec §2.3 – §2.4)
Run submissions accept `Idempotency-Key`; replay returns the original Run for byte-equal bodies, `422 IDEMPOTENCY_KEY_BODY_MISMATCH` for divergent bodies. Webhooks are HMAC-signed with `signed_at` timestamp (5 min replay window). New URLs go through a registration challenge before they receive deliveries; secret rotation has a 24 h grace period.

### Content-addressed assets (spec §3.5)
All binary blobs (antenna patterns, site photos, BYO rasters, building shapefiles, large measurement CSVs) use `sha256:` identifiers. Initiate → PUT (direct, < 50 MB) or parallel-PUT (multipart, ≥ 50 MB at 16 MiB parts) → complete. Duplicate uploads short-circuit. Reference-counted lifecycle; orphaned assets purged after 7 days. See worked walkthrough: [asset-upload.md](docs/superpowers/specs/examples/asset-upload.md).

### Canonical-vs-derivative artifacts (spec §6.1, §8.2)
Outputs are split into two classes:

- **Canonicals** — `link_budget`, `path_profile`, `geotiff`, `voxel`, `stats`, `best_server_raster`, `fidelity_tier_raster`, `point_query`, plus link-type semantic outputs — persist to per-class TTL.
- **Derivatives** — `kmz`, `png_with_worldfile`, `geojson_contours`, `geotiff_stack`, `rendered_cross_section`, voxel slices — regenerate from canonicals on demand and cache 24 h.

Re-styling outputs (different colormap, contour thresholds, output CRS) goes through `POST /v1/runs/{id}/artifacts:rederive` instead of re-running propagation.

| Class | Default TTL | Notes |
|---|---|---|
| Run record + JSON metadata | indefinite | Kept with Run |
| `geotiff`, `best_server_raster`, `fidelity_tier_raster` | 30 d | LZW + predictor=3 compression |
| `voxel` | 7 d | NetCDF + zlib + 0.5 dB quantization default |
| Derivatives | 24 h | Regenerated from canonicals |

`POST /v1/runs/{id}/pin` overrides class TTLs; Comparisons auto-pin referenced Runs (capped by `max_pinned_runs`, default 100). Per-key storage quota defaults to 10 GiB.

### Voxel slicing (spec §6.6)
A 5 GB voxel doesn't have to be downloaded to ask "what's coverage at 90 m AGL?" — the slice endpoint returns just the requested altitudes (or bbox subset), in `geotiff`, `geotiff_stack`, `voxel_subset`, or `json_point_grid` format.

### Tx/Rx specification & frequency authority (spec §4.0)
Both ends of every link are `(Site or coord) × Equipment Profile`. The link frequency is taken from the **Tx** Equipment Profile's radio; the Rx contributes sensitivity, antenna, mount, and cable loss. Op-specific pairing rules (notably: Op C requires exactly one `rx_template` per distinct `link_type` in the Tx set) are enforced server-side with structured errors.

### Polarization mismatch (spec §4.5)
Concrete base-mismatch table (V/H/RHCP/LHCP/slant-45/dual) plus a path-aggregated depolarization factor sourced from `ClutterTable.depolarization_factor_per_class`. The 3 dB floor in dense canopy is explicit; matching polarization (base ≤ 3 dB) is exempt from the floor. The link budget records base, depolarization factor, and effective mismatch separately.

### Predicted-vs-observed reporting (spec §7)
Attach a Measurement Set to a Run and the engine produces an `error_db` per matched point, plus aggregates (`mean`, `median`, `rmse`, `max_abs`, `bias_direction`, per-clutter-class breakdown). Filter rules are dimensionally coherent: frequency tolerance defaults to half the radio's bandwidth; metric coherence is enforced (LoRa accepts `rssi`/`snr`; LTE accepts `rsrp`/`rsrq`/`sinr`; etc.). No cross-metric conversion. Multiple measurement sets attached to one Run produce multiple report blocks.

### Single-tenant Docker-Compose deployment (spec §2.2, §8.5)
Bundled global baseline (SRTM-30 DTM, ESA WorldCover land-cover) plus the [standard profile library](docs/superpowers/specs/seed/standard-profile-library.json) plus system ClutterTables seed on first boot of the catalog DB and geo store. Local mode is fully offline-capable. Asset upload/download URLs proxy through the API service for parity with cloud deployments.

### Observability (spec §8.6)
Structured JSON logs per request and per pipeline stage; Prometheus-style metrics (runs by status/operation, queue depth, worker stage timings, artifact-store bytes, GC sweep stats); per-Run trace retrievable from the Run record; `/healthz` (process liveness) and `/readyz` (dependency reachability).

### Error & warning catalog (spec Appendix D)
Every machine-readable code is enumerated:

- **Errors** — request rejections and run failures (`RX_TX_FREQ_MISMATCH`, `OP_C_RX_TEMPLATE_MISSING`, `FIDELITY_FLOOR_NOT_MET`, `IDEMPOTENCY_KEY_BODY_MISMATCH`, `STORAGE_QUOTA_EXCEEDED`, `LAYER_GONE`, etc.)
- **Warnings** — PARTIAL completions (`FIDELITY_DEGRADED`, `MODEL_OUT_OF_NOMINAL_FREQ`, `CLUTTER_TABLE_TAXONOMY_FALLBACK`, `POLARIZATION_DEFAULTED`, `DSM_GAP`, `FETCHED_LAYER_PARTIAL`, `RESOLUTION_EXCEEDS_DATA`)
- **Filter reasons** — informational, on PvO and grid sampling (`OBSERVED_METRIC_MISMATCH`, `OBSERVATION_OUT_OF_GEOMETRY`, `OBSERVATION_OUT_OF_FREQ_TOLERANCE`)

These codes are mirrored verbatim in the OpenAPI `ProblemDetail.code` enum so clients can branch programmatically.

## Conventions

- Cite spec sections as `§N.M` — line numbers move when the doc evolves.
- Timestamps: RFC 3339 UTC. Frequencies: MHz unless suffixed (`_khz`, `_ghz`). Altitudes carry an explicit `altitude_reference: "agl" | "amsl"`.
- Hashes: SHA-256, lowercase hex; `sha256:` prefix when used as identifiers.
- Diagrams: mermaid, embedded in the spec markdown — nine included (service topology, mode-flow sequence, asset-upload sequence, reference graph ER, run-lifecycle state, 12-stage pipeline, model auto-select, fidelity-tier resolution, retention timeline).

See [CLAUDE.md](CLAUDE.md) for working agreements with AI assistants.

## Cross-artifact sync — required for every spec change

The design spec is canonical, but **four surfaces must agree** before the contract is implementable. This is the single largest maintenance risk in the repo today: drift between them silently breaks code-gen and confuses implementers, and one concept commonly fans out to 10+ edit sites.

| Surface | File | Role |
|---|---|---|
| Design spec markdown | [`docs/superpowers/specs/2026-04-25-rf-site-planning-api-design.md`](docs/superpowers/specs/2026-04-25-rf-site-planning-api-design.md) | Source of truth — narrative + tables |
| OpenAPI 3.1 | [`docs/superpowers/specs/2026-04-25-rf-site-planning-api.openapi.yaml`](docs/superpowers/specs/2026-04-25-rf-site-planning-api.openapi.yaml) | Endpoint contract + every entity schema |
| JSON Schema 2020-12 | [`docs/superpowers/specs/2026-04-25-analysis-requests.schema.json`](docs/superpowers/specs/2026-04-25-analysis-requests.schema.json) | Op A–E request bodies |
| Seed library / scenarios / test vectors | [`docs/superpowers/specs/seed/`](docs/superpowers/specs/seed/) | Bundled catalog + runnable fixtures + golden numerical vectors |

**When you change a concept with a machine-readable representation, propagate the change across all four surfaces in the same commit.** Use this checklist:

- **New catalog entity** — §3.2 entity table (bump count) · §3.x detail subsection · §3.6 reference graph · §2.5 endpoint inventory · spec change log · OpenAPI path family · OpenAPI component schema · OpenAPI page wrapper · JSON Schema `InlineX` def · `RefOrInlineX` def · any `AnalysisCommon` hook.
- **New error / warning / filter code** — spec Appendix D · OpenAPI `ProblemDetail.code` (or `warnings.items.code`) enum · spec change log.
- **New enum value** (`LinkType`, `SensitivityClass`, `LicenseClass`, `FidelityTier`, propagation/fading model, polarization, operation, run-status, asset purpose, output key) — spec narrative · OpenAPI enum · JSON Schema enum · `examples` lists.
- **New pipeline stage or stage behavior** — §4.1 prose · §4.1 mermaid · Appendix A row if visible there.

Before claiming a spec change is complete, re-run the structural validators:

```bash
pip install pyyaml jsonschema      # one-time
python scripts/check-sync.py
```

The script (`scripts/check-sync.py`) loads the OpenAPI YAML, the JSON Schema, the standard profile library, and every scenario / test-vector JSON; validates every scenario's `request` block against the JSON Schema; and verifies the antenna-pattern asset manifest hashes match the bytes on disk. CI runs the same script on every PR via [`.github/workflows/spec-sync.yml`](.github/workflows/spec-sync.yml).

For numerical changes touching the link-budget / propagation / polarization formulas, also re-run the golden test vectors arithmetic check (Python snippet in [`seed/test-vectors/README.md`](docs/superpowers/specs/seed/test-vectors/README.md)).

The same rule is duplicated in [CLAUDE.md](CLAUDE.md) for AI assistants. Treat the duplication as load-bearing — it lives in the README for humans browsing the repo and in CLAUDE.md so AI sessions pick it up automatically without needing to discover this README first.

## Spec navigation

| Topic | Spec section |
|---|---|
| API contract: mode selection, idempotency, error model | §2.3 |
| Webhooks: signing, registration challenge, secret rotation | §2.4 |
| Endpoint inventory | §2.5 |
| Catalog: identity, sharing, versioning, soft-delete | §3.1 |
| First-class entity table (10 entities) | §3.2 |
| Run record fields | §3.3 |
| Standard profile library (system-owned, shared, read-only) | §3.4 |
| Assets — content-addressed binary blobs | §3.5 |
| Reference graph (ER diagram) | §3.6 |
| Tx/Rx model, frequency authority, per-Op pairing | §4.0 |
| 12-stage canonical pipeline | §4.1 |
| Model plugin contract; supported models; auto-select | §4.2 – §4.4 |
| Polarization mismatch (table + depolarization formula) | §4.5 |
| Geo data layer types; bundled baseline; AOI pack lifecycle | §5.1 – §5.3 |
| Adaptive fidelity tiers (T0–T4); floors and coverage | §5.4 |
| Coordinate systems and projections; BYO validation | §5.5 – §5.6 |
| Output artifacts: canonical vs derivative | §6.1 |
| Link-type plugin contract (open LinkType, bundled plugins) | §4.6 |
| Link-type semantic outputs (LoRa/LTE/drone-C2/RTK) | §6.2 |
| Color mapping; point queries | §6.3 – §6.4 |
| Multi-link Op C aggregation | §6.5 |
| Voxel slicing | §6.6 |
| Re-derivation flow | §6.7 |
| Measurement Set entity; ingest; predicted-vs-observed | §7 |
| Run lifecycle states; cancellation latency | §8.1 |
| Per-class retention; pinning; storage quota; opt-in dedup | §8.2 |
| Reproducibility and replay | §8.3 |
| Auth adapter contract; rate limiting | §8.4 |
| Local-mode constraints | §8.5 |
| Observability: logs, metrics, health | §8.6 |
| Engine version & change management | §8.7 |
| Performance characterization | §8.8 |
| Large-data transport (uploads, downloads, slicing, refresh) | §8.9 |
| Operation × Output compatibility matrix | Appendix A |
| Frequency band coverage by link type | Appendix B |
| Definitions | Appendix C |
| Errors, warnings, filter reasons | Appendix D |
| v1 → v2 change log | tail of spec |

## Status snapshot

| Layer | State |
|---|---|
| Design spec | Draft v3 — ready to implement |
| JSON Schema | Draft 2020-12, derived from spec |
| OpenAPI | 3.1, version `0.2.0-draft`, derived from spec |
| Examples | 5 op walkthroughs + asset upload |
| Seed library | 21 antennas, 18 radio profiles, 23 equipment profiles, 2 clutter tables, 2 bundled antenna-pattern asset files |
| Diagrams | 9 mermaid (service topology, mode flow, asset upload, reference graph, run lifecycle, 12-stage pipeline, model auto-select, fidelity-tier resolution, retention timeline) |
| Auto-memory | seeded for AI-assisted continuation across sessions |
| Implementation | Not started |

## Roadmap

Implementation planning happens in a separate session. The likely next deliverables are:

1. **Implementation plan** — tech-stack decisions (language, web framework, geo libraries, propagation model implementations, queue, DB, artifact store), build sequencing, milestones, risk identification.
2. **Reference Postman / Insomnia collection** generated from the OpenAPI.
3. **First implementation milestone** — likely the catalog service + asset model, since they unblock everything else and have the cleanest surface to test against.
