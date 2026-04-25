# RF Site Planning API — Design Specification

**Date:** 2026-04-25
**Status:** Draft v2 — pending user review
**Architecture approach:** Pluggable model registry + pipeline-stage engine
**Revision note:** v2 incorporates Tx/Rx frequency authority, three-tier fidelity reporting, content-addressed assets, canonical-vs-derivative artifact model, per-class retention, and an explicit error/warning catalog. See change log at end.

---

## 1. Purpose & Scope

### Purpose

A self-hosted, single-tenant API service that performs RF propagation analysis for site planning. The system targets field engineers placing **autonomous drone docks**, **sub-GHz IoT endpoints** (e.g., camera traps, fence/gate sensors, animal collars), **LoRa gateways**, **GNSS RTK base stations**, and **LTE backhaul equipment** in remote/protected areas. Wildlife-protection deployments are the primary driver of v1 design — see worked examples — but no spec primitive is wildlife- or vendor-specific. Representative questions the system answers:

- "From this candidate gateway site, what's the LoRa coverage to the IoT-sensor network in this AOI?"
- "Will the 2.4 GHz drone C2 link hold across this flight envelope at 60–120 m AGL?"
- "Of these five candidate dock sites, which gives the best combined LoRa + LTE + RTK coverage?"
- "Does observed RSSI at deployed sensors match what we predicted six months ago?"

### In scope

- Predictive RF coverage and link-budget computation across:
  - LoRa (sub-GHz ISM, 868/915 MHz)
  - LTE (600 MHz–3.5 GHz)
  - 2.4/5.8 GHz drone C2/video
  - GNSS RTK correction links
- Five analysis operations: point-to-point, area heatmap, multi-link site report, multi-Tx best-server, 3D/volumetric coverage.
- Adaptive geo-data fidelity (terrain only → DTM+clutter → DSM → DSM+buildings).
- Field-measurement storage and predicted-vs-observed reporting.
- Self-hosted, Docker-packaged deployment with a fully offline local mode.
- Standard profile library and global baseline geo-data shipped with the system.

### Out of scope (v1)

- Real-time spectrum analysis.
- Real-time / streaming measurement ingest (batch and chunked-append only).
- Network optimization beyond point-to-point (frequency planning, interference management).
- Regulatory licensing tooling.
- A first-party web UI (a thin reference client may exist but is not the product).
- Automatic model self-calibration. Measurement storage and predicted-vs-observed reporting are in scope; using measurements to bias models is deferred.
- 6 GHz / 60 GHz unlicensed bands (Wi-Fi 6E/7, 802.11ay).

### System context

The API is the only product surface. Callers are:
- Operational tools driven by field engineers.
- Automation pipelines (e.g., a deployment-planning workflow that scores candidate sites).
- Ad-hoc scripts.

All callers authenticate with an API key. Single-tenant deployment with multiple keys per tenant; per-entity sharing flags grant cross-key visibility within the tenant.

### Global conventions

- All timestamps are RFC 3339 UTC (e.g., `2026-04-25T14:00:00Z`).
- Frequencies are MHz unless field name specifies otherwise (`_khz`, `_ghz`).
- Altitudes carry an explicit `altitude_reference: "agl" | "amsl"`.
- Distances are meters unless field name specifies otherwise (`_km`).
- Powers and gains are dB-domain (dBm absolute, dB relative, dBi for antenna gain).
- All hashes are SHA-256, lowercase hex, prefixed `sha256:` when used as identifiers.

---

## 2. Service Topology & API Contract

### 2.1 Service topology

The system is composed of cooperating services, packaged together as a Docker Compose stack for local/edge deployment and orchestrated for scaled deployment using the same images.

```mermaid
flowchart TD
    Caller([External Caller]) -->|HTTP/JSON + X-Api-Key| Gateway[API Gateway<br/>auth · rate-limit]
    Gateway --> Catalog[Catalog Service<br/>entities · sharing · refs]
    Gateway --> Orchestrator[Job Orchestrator<br/>sync routing · async lifecycle · webhooks]
    Orchestrator --> Queue[(Message Queue)]
    Queue --> Workers[Compute Worker Pool<br/>pluggable models · pipeline stages]
    Workers --> DB[(Relational DB<br/>catalog · runs · measurements)]
    Workers --> Artifacts[(Artifact Store<br/>GeoTIFFs · voxels · KMZ · assets)]
    Workers --> Geo[Geo Data Service<br/>DTM · DSM · clutter · buildings<br/>AOI packs · BYO uploads]
    Catalog --> DB
    Geo --> Artifacts
```

### 2.2 Deployment shape

- All services are packaged as one Docker Compose stack for local/edge use (laptop, field box). The same images are orchestrated for scaled deployment.
- A bundled global baseline (DTM at SRTM-30-class resolution, coarse global land-cover) and the standard profile library (default antennas, radio profiles, equipment profiles, system ClutterTables) are seeded on first boot of the catalog DB and geo store.
- Local mode is fully offline-capable once seeded.

### 2.3 API contract shape

- **Style.** REST, resource-oriented, JSON. OpenAPI-described. Versioned URL prefix (`/v1`).
- **Auth.** API key in `X-Api-Key` header. Multiple keys per tenant; sharing flag per catalog entity grants visibility across keys within the tenant. Auth is implemented behind a pluggable adapter interface (§8.4); v1 ships only the API-key adapter.
- **Mode selection.** Each analysis endpoint accepts `mode=sync|async|auto` (default `auto`). When `auto`, the orchestrator selects `async` if any of:
  - `operation ∈ {area, multi_link, multi_tx, voxel}`,
  - estimated output cell count > `auto_async_cell_threshold` (default **250,000**),
  - declared geometry exceeds `auto_async_area_km2` (default **100**).
  
  The chosen mode is echoed as `mode_executed` in the response and recorded on the Run record.
- **Sync budget.** Sync responses are bounded by `sync_budget_seconds` (default **25**, kept under typical proxy/ALB timeouts). On overrun the run is auto-promoted to async and the response returns `202 {run_id, status_url, mode_executed: "async", reason: "sync_budget_exceeded"}`. Async responses always return `{run_id, status_url, mode_executed: "async", optional webhook_url}`.
- **Reference shape.** Any field that names a catalog entity (Site, Radio Profile, Antenna, Equipment Profile, AOI Pack, Operating Volume, Measurement Set, ClutterTable, Comparison) accepts either a reference or a fully inlined object. Reference shape:
  ```
  { ref: <name>, owner?: "self" | "shared", version?: <int> | "latest" }
  ```
  Defaults: `owner = "self"`, `version = "latest"`. Cross-key references (`owner = "<key-id>"`) are **not** supported in v1; sharing is governed only by the entity's `share` flag (`private` | `shared`). Inlined objects are NOT persisted to the catalog. Mixed forms are allowed in the same request.
- **Output selection.** Each analysis request includes an `outputs` array declaring which artifacts to produce (e.g., `["link_budget", "geotiff", "geojson_contours", "kmz", "stats"]`). The engine produces only what is requested. Derivatives (§6) are produced eagerly on submit and may also be produced on demand later from the persisted canonicals (§8.9).
- **Idempotency.** Run submissions accept `Idempotency-Key`:
  - Same key + same key_id + byte-equal body → returns the original Run in whatever terminal state it reached (including FAILED, CANCELLED, EXPIRED).
  - Same key + same key_id + different body → `422 IDEMPOTENCY_KEY_BODY_MISMATCH` with the original Run id in the response.
  - Same key + different key_id → treated as unrelated.
  - Keys are remembered for `idempotency_window_days` (default **7**); subsequent reuse creates a new Run.
- **Error & warning model.** Standard problem-detail JSON; codes enumerated in Appendix D. Runs that succeed at degraded data fidelity return `warnings[]` and status `PARTIAL`; runs at the AOI's maximum possible fidelity return `COMPLETED`.
- **Pagination & filtering.** List endpoints (sites, antennas, runs, etc.) paginate with cursors and filter by name, owner, share-state, tag.

The mode-selection flow, including the auto-promotion of an overrunning sync request to async:

```mermaid
sequenceDiagram
    autonumber
    actor Caller
    participant API as API Gateway / Orchestrator
    participant Worker

    Caller->>API: POST /v1/analyses/{op} {mode, ...}

    alt resolves to async (requested or auto-selected)
        API-->>Caller: 202 {run_id, status_url, mode_executed: async, reason: requested}
        API->>Worker: enqueue
        Worker-->>API: terminal state
        opt webhook_url present
            API-)Caller: POST <webhook_url> (HMAC-signed)
        end
    else sync, completes within sync_budget_seconds
        API->>Worker: enqueue
        Worker-->>API: terminal state (under 25 s)
        API-->>Caller: 200 {Run record, mode_executed: sync}
    else sync, exceeds sync_budget_seconds
        API->>Worker: enqueue
        Note over API,Worker: 25 s elapses; run still RUNNING
        API-->>Caller: 202 {run_id, status_url, mode_executed: async,<br/>reason: sync_budget_exceeded}
        Worker-->>API: terminal state (run continues normally)
    end
```

### 2.4 Webhooks

- Async submissions MAY include `webhook_url`. The orchestrator POSTs to it on terminal-state transitions.
- Payload: `{run_id, status, signed_at, artifacts_url, warnings, error}`. `signed_at` is RFC 3339 UTC.
- **Signing.** Header `X-Signature: HMAC-SHA256(secret, signed_at + "." + body)`. Receivers MUST reject `signed_at` deltas > 5 minutes from local time.
- **Registration challenge.** First time a `webhook_url` is seen for a tenant, the orchestrator POSTs `{challenge: <random>}` and requires the URL to echo it back within 5 seconds. URLs that fail challenge are rejected. Subsequent submissions to a verified URL skip the challenge for `webhook_verification_ttl_days` (default **30**).
- **Secret rotation.** `POST /v1/webhooks/secrets:rotate` produces a new secret. Both old and new are accepted for **24 h** so receivers can roll over without downtime.
- Delivery retried with exponential backoff on non-2xx for a bounded window (default 6 attempts over 1 hour).

### 2.5 Endpoint inventory

| Method | Path | Sync/Async | Required scope |
|---|---|---|---|
| `POST` `GET` `PATCH` `DELETE` | `/v1/sites` (and `/{id}`) | sync | `catalog.*` |
| `POST` `GET` `PATCH` `DELETE` | `/v1/antennas` (and `/{id}`) | sync | `catalog.*` |
| `POST` `GET` `PATCH` `DELETE` | `/v1/radio-profiles` (and `/{id}`) | sync | `catalog.*` |
| `POST` `GET` `PATCH` `DELETE` | `/v1/equipment-profiles` (and `/{id}`) | sync | `catalog.*` |
| `POST` `GET` `PATCH` `DELETE` | `/v1/aoi-packs` (and `/{id}`) | sync (create may be async) | `catalog.*` |
| `POST` `GET` `PATCH` `DELETE` | `/v1/clutter-tables` (and `/{id}`) | sync | `catalog.*` |
| `POST` `GET` `PATCH` `DELETE` | `/v1/operating-volumes` (and `/{id}`) | sync | `catalog.*` |
| `POST` `GET` `PATCH` `DELETE` | `/v1/measurements` (and `/{id}`) | sync | `measurements.*` |
| `POST` | `/v1/measurements/{id}:append` | sync | `measurements.write` |
| `POST` `GET` `PATCH` `DELETE` | `/v1/comparisons` (and `/{id}`) | sync | `catalog.*` |
| `POST` | `/v1/assets:initiate` | sync | `catalog.write` |
| `POST` | `/v1/assets/{id}:complete` | sync | `catalog.write` |
| `POST` | `/v1/assets/{id}:abort` | sync | `catalog.write` |
| `GET` | `/v1/assets/{id}` (metadata) | sync | `catalog.read` |
| `POST` | `/v1/analyses/p2p` | sync (default) | `runs.submit` |
| `POST` | `/v1/analyses/area` | sync or async | `runs.submit` |
| `POST` | `/v1/analyses/multi_link` | sync or async | `runs.submit` |
| `POST` | `/v1/analyses/multi_tx` | sync or async | `runs.submit` |
| `POST` | `/v1/analyses/voxel` | async (default) | `runs.submit` |
| `GET` | `/v1/runs/{id}` | sync | `runs.read` |
| `GET` | `/v1/runs/{id}/status` | sync | `runs.read` |
| `DELETE` | `/v1/runs/{id}` (cancel) | sync | `runs.cancel` |
| `POST` | `/v1/runs/{id}/replay` | sync or async | `runs.submit` |
| `POST` | `/v1/runs/{id}/pin` `…/unpin` | sync | `runs.write` |
| `GET` | `/v1/runs/{id}/artifacts/{key}` (metadata) | sync | `runs.read` |
| `GET` | `/v1/runs/{id}/artifacts/{key}/url` (refresh) | sync | `runs.read` |
| `POST` | `/v1/runs/{id}/artifacts/{key}:materialize` | sync | `runs.read` |
| `POST` | `/v1/runs/{id}/artifacts:rederive` | sync | `runs.read` |
| `GET` | `/v1/runs/{id}/artifacts/voxel/slice` | sync | `runs.read` |
| `POST` `GET` `DELETE` | `/v1/webhooks` (and `/{id}`) | sync | `admin` |
| `POST` | `/v1/webhooks/secrets:rotate` | sync | `admin` |
| `GET` | `/healthz` `/readyz` `/metrics` | sync | (none) |

---

## 3. Catalog Data Model

### 3.1 Identity, sharing, versioning

- **Natural key:** `(owner_api_key, name, entity_type)`.
- **Stable internal ID:** survives renames; used in references that should not break on rename.
- **Sharing:** every entity has a `share` flag with values `private` (default — visible only to the creating key) or `shared` (readable by any key in the tenant; only the owner can write). Cross-key referencing by key id is not supported.
- **Versioning:** Antenna, Radio Profile, Equipment Profile, Site, AOI Pack, ClutterTable, Operating Volume, and Measurement Set version on edit. References can pin to a specific version (`version: <int>`) or float to `"latest"`. Runs always resolve to a specific version, recorded in `Run.inputs_resolved`.
- **`inputs_resolved` freeze point:** frozen at the SUBMITTED transition, after orchestrator validation, before QUEUED. Catalog edits after the freeze point have no effect on the in-flight run. The exact freeze time is recorded on the Run as `inputs_resolved_at`.
- **Soft delete:** deletes mark records hidden but do not break runs that reference them. Soft-deleted shared entities are hidden from list endpoints and return `404` on `GET` by name; resolution by stable internal ID continues to work for in-flight runs and historical `inputs_resolved`. Hard delete only via explicit purge.
- **Tags:** free-form tags on Site, AOI Pack, Equipment Profile, Operating Volume, Comparison.

### 3.2 First-class entities (9 total)

| Entity | Required fields | Notable optional fields | Purpose |
|---|---|---|---|
| **Site** | `name`, `lat`, `lon` | `ground_elevation_override_m`, `default_equipment_refs[]`, `notes`, `tags[]`, `photo_asset_ref` | Named geographic point. `default_equipment_refs[]` lists Equipment Profiles intended to be deployed at this site (e.g., a LoRa gateway + LTE backhaul + RTK base + 2.4 GHz drone C2 colocated at one tower). Op C uses these by default; analysis requests may override. |
| **Radio Profile** | `name`, `link_type` (string; `generic` is built-in, additional values registered by link-type plugins — see §4.6), `freq_mhz`, `bandwidth_khz`, `tx_power_dbm`, `rx_sensitivity_dbm` | Per-plugin extension fields. Bundled plugins ship `lora` (`spreading_factor`, `coding_rate`), `lte` (`band`, `earfcn`), `drone_c2` (`mode_label`), `rtk` (`mode_label`); each plugin's contract names the fields it consumes. Plus `modulation`, `fade_margin_db_target` (used by `generic`), `propagation_model_pref` (auto / explicit). | RF parameters; antenna-agnostic. |
| **Antenna** | `name`, `kind` (`parametric` / `pattern_file`), `gain_dbi`, `polarization`, `applicable_bands: [{min_mhz, max_mhz}, …]` | `applicable_polarizations[]`. Parametric: `pattern_type` (omni / sector), `h_beamwidth_deg`, `v_beamwidth_deg`, `electrical_downtilt_deg`. File: `format` (msi/adf/ant/csv), `pattern_asset_ref`. | Antenna spec; orientation comes from Equipment. The engine warns (`MODEL_OUT_OF_NOMINAL_FREQ`) at use within ±10 % of the declared band edge and fails (`ANTENNA_OUT_OF_BAND`) at >25 %. |
| **Equipment Profile** | `name`, `radio_ref`, `antenna_ref`, `mount_height_m_agl`, `cable_loss_db` | `cable_loss_curve: [{freq_mhz, loss_db}]` (overrides scalar via piecewise-linear interpolation), `azimuth_deg`, `mechanical_downtilt_deg`, `mfr`, `model`, `notes` | Deployable bundle of radio + antenna + mounting. `cable_loss_db` is evaluated at the bound radio's center frequency. |
| **AOI Pack** | `name`, `bbox` (south, west, north, east) | `dtm_ref`, `dsm_ref`, `clutter_ref`, `buildings_ref`, `clutter_table_ref`, `source` (bundled / byo / fetched), per-layer `upstream_source`, `upstream_version`, `acquired_at`, `content_sha256`, `resolution_m`, `notes` | Region with attached geo-data layers. Per-layer provenance fields (§5.3). |
| **ClutterTable** | `name`, `taxonomy_id`, `class_table` (mapping class_id → per-band attenuation in dB) | `depolarization_factor_per_class` (mapping class_id → `d ∈ [0, 1]`), `notes`, `applicable_freq_bands` | Per-class attenuation table; depolarization factor consumed by polarization mismatch (§4.5). |
| **Operating Volume** | `name`, `bbox` *or* `polygon`, `altitude_min_m_agl`, `altitude_max_m_agl` | `altitude_step_m`, `duration_estimate_min`, `home_site_ref`, `host_site_ref`, `notes` | A 3D region of interest for volumetric coverage analysis. Drone-flight-envelope is the primary use case (Op E with `home_site_ref` pointing at the drone dock or launch site); also covers tower vertical-pattern surveys, tethered-balloon links, and any other case needing per-altitude evaluation. `home_site_ref` names a return-to-home / launch-recovery anchor (drone-specific semantics). `host_site_ref` names the operational anchor for non-recovering deployments. Both are optional and orthogonal — typically the same Site, sometimes different, sometimes neither. |
| **Measurement Set** | `name`, `points[]` where each `= {lat, lon, alt_m_agl, freq_mhz, observed_signal_dbm, observed_metric, timestamp, source}` | `ordered: bool` (default `false`; tracks set this `true`), per-point `seq: int` (when `ordered`), `device_ref`, `site_ref` *or* `aoi_ref`, `notes`, per-point `bandwidth_khz`, `uncertainty_db`, `tags` | Stored field RSSI/RSRP observations. A point cloud (camera traps) by default; tracks set `ordered: true` and supply `seq` per point. |
| **Comparison / Plan** | `name`, `run_ids[]` | `notes`, `winner_run_id`, `decision_rationale`, `decided_at` | Captures a real placement decision with the runs that informed it. |

### 3.3 Run record (separate, immutable)

A Run is a first-class persisted record but not a catalog entity (no name-based identity, no sharing). Fields:

- `id`, `submitted_by_key`, `submitted_at`, `inputs_resolved_at`, `completed_at`, `status`
- `operation` (`p2p` / `area` / `multi_link` / `multi_tx` / `voxel`)
- `mode_requested` (`sync` / `async` / `auto`), `mode_executed` (`sync` / `async`)
- `inputs_resolved` — frozen, fully-inlined snapshot of every reference at the freeze point
- `inputs_resolved_sha256` — SHA-256 of the canonicalized `inputs_resolved` (used by opt-in dedup, §8.2)
- `engine_version`, `engine_major`, `models_used[]` (with model plugin versions), `data_layer_versions`
- `fidelity_tier_dominant`, `fidelity_tier_min`, `fidelity_tier_max`, `fidelity_tier_max_possible`
- `output_artifact_refs[]` — see §8.9 for shape
- `warnings[]`, `error` (if failed) — codes per Appendix D
- `pinned` (boolean; suppresses canonical-artifact TTL expiry)
- `cancellation_reason` (`user` | `expired` | `sync_budget_exceeded` | null)
- `comparison_id` (if part of one)
- `replay_of_run_id` (if produced via the replay endpoint), `replay_engine_major_drift` (if cross-major)

### 3.4 Standard profile library

Ships under a reserved owner key (`system`), all entries `share=shared`, read-only. The library carries vendor-specific instances built on top of the generic catalog primitives — Antenna, Radio Profile, Equipment Profile — without those primitives encoding any vendor concept. Each seed entry is a concrete instance of the same shapes a deployment operator would create themselves.

Bundled categories with representative entries (the list expands as plugins land; this is illustrative, not exhaustive):

- **Antennas.** Omni 2/3/6/8 dBi reference; generic sector 60°/90°/120°; sub-GHz IoT endpoint patches; drone/RTK 2.4 GHz approximations.
- **Radio Profiles.** LoRa-868-EU, LoRa-915-US (LoRa link-type plugin); LTE common bands (LTE plugin); generic 2.4 GHz drone C2 (drone_c2 plugin); 2.4 GHz RTK base/rover (rtk plugin).
- **Equipment Profiles — autonomous drone docks** (built on `drone_c2` Radio + 2.4 GHz dock antenna + `Site` for the dock location). Seed entries cover specific dock products such as DJI Dock 2; operators clone-and-customize for other vendors.
- **Equipment Profiles — sub-GHz IoT endpoints** (built on a LoRa Radio Profile + an endpoint antenna). Seed entries: `camera-trap-lora-rx`, `fence-sensor-lora-rx`, `gate-sensor-lora-rx`, `wildlife-collar-lora-tx`. Each is an Equipment Profile — a deployment-shaped concept built on the generic catalog primitives, not a baked-in entity type.
- **Equipment Profiles — LTE backhaul** (LTE Radio + handset/CPE antenna).
- **Equipment Profiles — RTK base/rover** (RTK Radio + 2.4 GHz omni). Seed entry covers DJI D-RTK 3 as an example concrete instance; the underlying primitives are vendor-neutral.
- **System ClutterTables.** ESA WorldCover and Copernicus CGLS taxonomies, pre-tuned per ITU-R P.833 / P.2108 across LoRa, LTE, 2.4, 5.8 GHz bands, with per-class `depolarization_factor` populated.

Operators clone-and-customize but cannot mutate `system`-owned entries. New device types — a different sensor product, a new dock vendor, a non-RF telemetry endpoint — are added by creating Equipment Profiles in the catalog; no spec change is needed unless the device introduces a fundamentally new link-type, in which case the operator (or a plugin author) registers a link-type plugin per §4.6.

### 3.5 Assets (binary blobs)

Assets are opaque binary blobs (antenna pattern files, site photos, building shapefiles, BYO rasters). Assets are **not** named/versioned/shared catalog entities — they are **content-addressed** (`asset_id = "sha256:<hex>"`) and lifecycle-managed by reference count.

**Identity & deduplication.** The asset id is derived from the SHA-256 of the bytes. Uploading identical content twice returns the same `asset_id` and skips the second upload entirely. Asset content is immutable.

**Upload flow.**

```
POST /v1/assets:initiate
  Body: { filename, content_type, size_bytes, sha256, purpose }
        purpose ∈ { antenna_pattern | site_photo | raster_dtm |
                    raster_dsm | raster_clutter | vector_buildings |
                    measurement_csv | generic }

  Response — already exists (same sha256 in store):
    { asset_id, already_exists: true, ready: true }
    (no upload required)

  Response — direct mode (size_bytes < 50 MB):
    { asset_id, mode: "direct",
      upload: { method: "PUT", url, headers, expires_at } }

  Response — multipart mode (size_bytes ≥ 50 MB):
    { asset_id, mode: "multipart",
      part_size_bytes: 16_777_216,                 # 16 MiB
      parts: [{ part_number, upload_url, expires_at }, …],
      complete_url, abort_url }

→ Caller PUTs bytes (one PUT for direct, parallel PUTs for multipart).

POST <complete_url>
  Body for direct: {}                  # server validated checksum on PUT
  Body for multipart: { parts: [{ part_number, etag }, …] }
  Response: { asset_id, content_type, size_bytes, sha256, ready: true }
```

Visualized:

```mermaid
sequenceDiagram
    autonumber
    actor Caller
    participant API as API Gateway
    participant Store as Artifact Store

    Caller->>API: POST /v1/assets:initiate<br/>{filename, content_type, size_bytes, sha256, purpose}

    alt sha256 already in store
        API-->>Caller: 200 {asset_id, already_exists: true, ready: true}
    else size_bytes < 50 MB (direct mode)
        API-->>Caller: 200 {asset_id, mode: direct,<br/>upload: {method: PUT, url, headers, expires_at}}
        Caller->>Store: PUT <upload.url> (bytes)
        Store-->>Caller: 200
        Caller->>API: POST /v1/assets/{id}:complete {}
        API-->>Caller: 200 {asset_id, sha256, size_bytes, ready: true}
    else size_bytes >= 50 MB (multipart mode)
        API-->>Caller: 200 {asset_id, mode: multipart,<br/>part_size_bytes: 16 MiB,<br/>parts: [...], complete_url, abort_url}
        loop for each part (parallel)
            Caller->>Store: PUT <parts[i].upload_url>
            Store-->>Caller: 200 ETag
        end
        Caller->>API: POST <complete_url><br/>{parts: [{part_number, etag}, ...]}
        API-->>Caller: 200 {asset_id, sha256, size_bytes, ready: true}
    end

    Note over API,Store: If complete not called within 24 h, upload aborted; parts reclaimed.
```

If `complete` is not called within 24 h of `initiate`, the upload is aborted and any uploaded parts are reclaimed.

**Reference & lifecycle.** Catalog entity fields named `*_asset_ref` carry an `asset_id`. An asset with no inbound references from any catalog entity (including soft-deleted ones) is purged after `asset_orphan_ttl_days` (default **7**). Inbound references from any live or soft-deleted catalog entity keep an asset alive; only hard-purge of the entity removes the reference.

**Local-mode parity.** In offline/Docker-Compose deployments, `upload_url` and `download_url` point back at the API service, which streams to/from a host-mounted volume. The client flow is identical.

### 3.6 Reference graph

Catalog reference relationships between the entities. **Solid edges are live references** — resolved at submission time, version-pinned in `Run.inputs_resolved`. **Dashed edges** show how a Run holds inlined snapshots of resolved entities; the snapshot is independent of the catalog after the SUBMITTED freeze (§3.1).

```mermaid
erDiagram
    Site               }o--o{ EquipmentProfile : "default_equipment_refs[]"
    Site               }o--o| Asset            : "photo_asset_ref"

    EquipmentProfile   }o--|| RadioProfile     : "radio_ref"
    EquipmentProfile   }o--|| Antenna          : "antenna_ref"

    Antenna            }o--o| Asset            : "pattern_asset_ref"

    AOIPack            }o--o{ Asset            : "dtm/dsm/clutter/buildings refs"
    AOIPack            }o--o| ClutterTable     : "clutter_table_ref"

    OperatingVolume    }o--o| Site             : "home_site_ref"
    OperatingVolume    }o--o| Site             : "host_site_ref"

    MeasurementSet     }o--o| Site             : "site_ref"
    MeasurementSet     }o--o| AOIPack          : "aoi_ref"
    MeasurementSet     }o--o| Asset            : "csv_asset_ref"

    Comparison         }o--o{ Run              : "run_ids[]"

    Run                }o--o{ Asset            : "output_artifact_refs[]"
    Run                }o..o{ Site             : "snapshot in inputs_resolved"
    Run                }o..o{ EquipmentProfile : "snapshot in inputs_resolved"
    Run                }o..o{ AOIPack          : "snapshot in inputs_resolved"
    Run                }o..o{ OperatingVolume  : "snapshot in inputs_resolved"
    Run                }o..o{ MeasurementSet   : "snapshot in inputs_resolved"
```

---

## 4. Engine Pipeline & Propagation Model Registry

### 4.0 Tx and Rx specification model

Every analysis is a function of one or more **transmitter ends** and one or more **receiver ends**. Both ends are specified using the same primitive: a `(Site or coordinate) × Equipment Profile` pair. The Equipment Profile carries the radio (which determines link type, frequency, power, sensitivity) plus the antenna, mount height, cable loss, azimuth, and tilt. Where the receiver is a *grid* rather than a single location, the caller supplies an `rx_template` Equipment Profile and the engine instantiates a virtual receiver of that template at every grid sample point.

**Frequency authority.** The link frequency is taken from the **Tx** Equipment Profile's radio. The Rx Equipment Profile contributes sensitivity, antenna gain/pattern, mount height, cable loss, and link-type semantics — its radio's `freq_mhz` is informational. If `|rx.radio.freq_mhz - tx.radio.freq_mhz|` exceeds `tx.radio.bandwidth_khz × 1.5 / 1000` MHz, validation fails at stage 2 with `RX_TX_FREQ_MISMATCH`. The same rule applies to `rx_template` in Ops B/C/D/E.

Per-operation Tx/Rx convention:

| Op | Tx side | Rx side |
|---|---|---|
| **A (P2P)** | One `tx_site` + `tx_equipment_ref` | One `rx_site` + `rx_equipment_ref` |
| **B (Area)** | One `tx_site` + `tx_equipment_ref` | One `rx_template` Equipment Profile applied at every grid sample at the template's `mount_height_m_agl` (or an `rx_altitude_override_m_agl` per-call) |
| **C (Multi-link)** | One `tx_site` + `tx_equipment_refs[]` (defaults to the site's `default_equipment_refs[]`); each evaluated independently | `rx_templates[]`, each entry an Equipment Profile keyed by the radio's `link_type`. **Exactly one Rx template per distinct `link_type` present in the Tx set.** Each Tx is paired with the Rx template whose `link_type` matches; multiple Tx of the same `link_type` share one Rx template. Unmatched Tx fail validation with `OP_C_RX_TEMPLATE_MISSING`. |
| **D (Multi-Tx)** | List of `tx_sites[]` with `equipment_ref` per site | One `rx_template` applied against all candidate Tx |
| **E (3D)** | One (or more) `tx_site` + `tx_equipment_ref` | One `rx_template` applied at every (grid sample, altitude) drawn from the Operating Volume (or an inline `bbox + altitudes[]`) |

Coordinates may be inlined in place of a Site reference (per §2.3 reference shape). Equipment Profiles may be inlined or referenced.

### 4.1 Canonical pipeline stages

Every analysis flows through a subset of these stages, in order:

1. **Resolve inputs.** Replace every catalog reference with a fully-inlined object, version-pinned. Snapshot becomes `Run.inputs_resolved`; `inputs_resolved_at` is stamped.
2. **Validate compatibility.** Frequency authority (§4.0), antenna applicable_bands (§3.2), polarization vs. radio band, AOI bounds vs. site location, mission altitudes vs. radio link type, etc.
3. **Plan geometry samples.** The operation-specific sampler emits the set of (Tx, Rx) sample pairs:
   - **Op A (point-to-point):** 1 pair.
   - **Op B (area):** 1 Tx × N Rx (grid over AOI at sensor altitude).
   - **Op C (multi-link site):** same as B, run K times — once per equipment-profile link at the site — combined into one report.
   - **Op D (multi-Tx best-server):** M Tx × N Rx; per-Rx the engine selects the winning Tx by margin and emits per-Tx sub-rasters as well.
   - **Op E (3D / volumetric):** 1 (or M) Tx × N Rx × L altitudes. Output shape is caller-selectable: stack of 2D rasters per altitude and/or a 3D voxel array.
4. **Load geo data.** Geo Data Service returns the merged tile stack covering the geometry: DTM (always — falls back to bundled baseline), DSM (if available), clutter raster (if available), building polygons (if available). Engine records which layers were resolved and the per-pixel fidelity tier.
5. **Build terrain profiles.** For each (Tx, Rx) sample pair, sample elevation along the great-circle path. If DSM present, that's the obstacle profile; if only DTM, terrain-only.
6. **Apply clutter overlay.** If clutter raster + ClutterTable present, accumulate per-pixel attenuation along the path based on land-cover class table.
7. **Apply antenna gains.** Look up Tx and Rx gain at the bearing+elevation of the path. Compute polarization mismatch loss per §4.5.
8. **Run propagation model.** The selected model plugin computes path loss given the profile, frequency, geometry, and any model-specific parameters.
9. **Aggregate link budget.** Total received power = Tx power + Tx gain − cable loss − path loss − clutter loss − polarization mismatch − Rx feeder loss + Rx gain. Compute fade margin vs. Rx sensitivity and link availability % from a fading model. Fading model selection: `auto` by default — Rayleigh in dense clutter / non-line-of-sight, Rician (with K-factor varying by clearance) in line-of-sight, log-normal shadowing applied per environment class. Caller can pin `fading_model` and `fading_params` per call; chosen model is recorded in the link budget.
10. **Emit link-type semantics.** If `link_type ≠ generic`, dispatch to the registered link-type plugin (§4.6). Bundled plugins: LoRa emits SNR/best-SF/time-on-air; LTE emits RSRP/RSRQ/SINR/MCS; drone C2 emits pass/fail and range envelope; RTK emits pass/fail and range envelope.
11. **Render artifacts.** Format the result into every canonical artifact required and every derivative the caller requested in `outputs[]`.
12. **Persist & finalize.** Write artifacts to artifact store, write Run record, trigger webhook if async.

The pipeline is the *logical* contract. Implementations may vectorize stages 5–9 across many sample pairs at once.

### 4.2 Model plugin contract

Every propagation model is a plugin implementing:

```
ModelCapabilities {
  name: str                            # e.g., "ITU-R P.1812"
  version: str
  freq_range_mhz: (min, max)
  scenario_suitability: {
     terrestrial_p2p: float,           # 0..1 score, 0 = not suitable
     terrestrial_area: float,
     air_to_ground: float,
     low_altitude_short_range: float,
     ionospheric: float,
     urban: float
  }
  required_data_tiers: {min, preferred}  # e.g., min=DTM, preferred=DTM+clutter
  parameters_schema: JSONSchema           # model-specific knobs
}

ModelInterface {
  capabilities() -> ModelCapabilities
  predict(profile, frequency, geometry, params, data_tier) -> PathLossResult
}
```

### 4.3 Models supported

Plugins committed:

- **ITU-R P.1812** — modern terrestrial point-to-area, 30 MHz–6 GHz. Recommended general default.
- **ITU-R P.526** — diffraction-focused, used as a building block.
- **ITM / Longley-Rice** — classic, well-validated, 20 MHz–20 GHz.
- **ITU-R P.528** — air-to-ground, 100 MHz–30 GHz. Used for drone-as-airborne-node and Op E with C2.
- **ITU-R P.530** — terrestrial line-of-sight microwave, point-to-point.
- **Free-space (Friis)** — sanity-check baseline.
- **Two-ray ground reflection** — short low-altitude links.

Per-class clutter overlay is applied as a separate pipeline stage (stage 6) on top of any model above when land-cover data is available.

### 4.4 Model auto-select strategy

When `propagation_model = auto` (the default), the engine picks per-call by:

1. Filter plugins whose `freq_range_mhz` covers the radio's frequency.
2. Score remaining plugins by `scenario_suitability[scenario]` where `scenario` is derived from `(operation, link_type, geometry)` — e.g., Op E with drone C2 → `air_to_ground`; Op B with LoRa from a tower → `terrestrial_area`.
3. Down-weight plugins whose `required_data_tiers.min` exceeds what the AOI provides.
4. Pick the highest-scoring plugin; tie-break by an explicit preference order configured per deployment.
5. The picked model is recorded in `Run.models_used` and surfaced in the response.

A caller can pin a specific model (`propagation_model = "p1812"`) to override auto-select. If the pinned model is unsuitable (out of frequency range, missing required data tier), the request fails with `PINNED_MODEL_OUT_OF_RANGE` or `PINNED_MODEL_DATA_TIER_INSUFFICIENT`.

### 4.5 Polarization mismatch

After Tx/Rx antenna gains are applied, the engine computes mismatch loss in two steps.

**Step 1 — base mismatch from polarization declarations.** Both ends declare `polarization` ∈ {V, H, RHCP, LHCP, slant-45, dual}. Base mismatch in dB:

| Tx \ Rx | V | H | RHCP | LHCP | slant-45 | dual |
|---|---|---|---|---|---|---|
| **V** | 0 | 20 | 3 | 3 | 3 | 0 |
| **H** | 20 | 0 | 3 | 3 | 3 | 0 |
| **RHCP** | 3 | 3 | 0 | 20 | 3 | 0 |
| **LHCP** | 3 | 3 | 20 | 0 | 3 | 0 |
| **slant-45** | 3 | 3 | 3 | 3 | 0 if aligned, 20 if orthogonal | 0 |
| **dual** | 0 | 0 | 0 | 0 | 0 | 3 |

**Step 2 — depolarization attenuation along the path.** Heavy clutter (canopy, multipath) depolarizes the signal, reducing effective cross-pol mismatch. Each `ClutterTable` row carries `depolarization_factor d_i ∈ [0, 1]` (default 0). The path-aggregated factor is

```
d = 1 − Π over classes i of (1 − d_i) ^ (L_i / L_total)
```

where `L_i` is the great-circle path length spent in class `i` and `L_total` is the full path length. If clutter data is absent, `d = 0`.

**Final loss.** When the base mismatch is already ≤ 3 dB (matching or near-matching pol), no attenuation is applied. Otherwise:

```
mismatch_loss_db =
  base_mismatch_db                                    if base_mismatch_db ≤ 3
  max(3, base_mismatch_db × (1 − d))                  otherwise
```

The 3 dB floor avoids implausibly clean cross-pol in dense canopy. The computed value, the base value, and the `d` used are recorded as separate lines in the link budget.

### 4.6 Link-type plugin contract

The `link_type` field on Radio Profiles is an open string. Only `generic` is built into the core engine; every other link-type — `lora`, `lte`, `drone_c2`, `rtk`, and any future addition (5G NR, satellite L/S, ham VHF/UHF, Wi-Fi mesh, etc.) — is registered by a link-type plugin. Bundled plugins ship by default; operators can install additional plugins without spec changes.

A link-type plugin declares:

```
LinkTypePluginCapabilities {
  link_type: str                    # e.g., "lora"; lowercase, snake_case
  version: str
  display_name: str                 # human-friendly, for error messages and UI

  # Radio Profile extension
  radio_profile_extension_schema: JSONSchema
                                    # additional Radio Profile fields this plugin
                                    # consumes (e.g., LoRa: spreading_factor)

  # Outputs the plugin can emit (canonical artifacts, named keys)
  declared_outputs: [
    { key: str,                     # e.g., "lora_best_sf"
      class: "canonical" | "derivative",
      content_type: str,
      description: str }
  ]

  # Acceptable measurement metrics for §7.3 metric coherence filter
  accepted_observed_metrics: [str]  # e.g., ["rssi", "snr"]

  # Default colormaps registered for this plugin's outputs
  declared_colormaps: { <name>: <ColorMap> }

  # Auto-select hint: which scenarios should the engine prefer for this link_type
  scenario_hints: { terrestrial_p2p, terrestrial_area, air_to_ground, ... }
}

LinkTypePluginInterface {
  capabilities() -> LinkTypePluginCapabilities

  # Stage 10: type-specific computation given the aggregated link budget
  emit(link_budget, outputs_requested, params) -> { artifacts, warnings }

  # Pre-flight validation hook (called during Stage 2)
  validate(radio_profile, equipment_profile, geometry) -> [warnings, errors]
}
```

**Resolution.** When a Radio Profile carries `link_type: X`, the engine looks up the plugin registered for `X`. If none is registered, validation fails at Stage 2 with `LINK_TYPE_NOT_REGISTERED`. The `generic` link-type is special — it is implemented in core and always available; it falls back to the generic stage-10 path (received-power + fade-margin only, no specialized outputs).

**Output keys.** Plugin-declared output keys are namespaced by the plugin (e.g., LoRa contributes `lora_snr_margin`, `lora_best_sf`, `lora_link_metrics`). Per-link-aggregation rules in §6.1 apply unchanged: `<key>.<link_type>` for Op C, `<key>.<tx_label>` for Op D.

**Versioning and reproducibility.** A plugin's version string flows through `Run.models_used[]` alongside propagation models, so reruns are reproducible against the exact plugin revision.

**Bundled plugins.** v1 ships `lora`, `lte`, `drone_c2`, `rtk`. Their declared outputs and accepted metrics are documented in §6.2 and §7.3 respectively; those sections are the authoritative reference for the bundled contracts.

---

## 5. Geospatial Data Model & Adaptive Fidelity

### 5.1 Layer types

| Layer | Format | Source examples | Stages used |
|---|---|---|---|
| **DTM** (bare-earth elevation) | Single-band raster (Float32, meters AMSL) | SRTM-30, Copernicus GLO-30, BYO | Stages 5, 8 (always required) |
| **DSM** (surface incl. canopy/buildings) | Single-band raster (Float32, meters AMSL) | Copernicus GLO-30 DSM, drone-derived photogrammetry, BYO | Stage 5 if present |
| **Clutter / Land-cover** | Categorical raster (UInt8/UInt16) + ClutterTable mapping class → attenuation per band | ESA WorldCover, Copernicus CGLS, BYO | Stages 6, 7 (depolarization) if present |
| **Buildings** | Vector (GeoJSON / GeoPackage / Shapefile) with `height_m` attribute | OSM, BYO | Stage 6 building loss; stage 5 obstacle profile if no DSM |

### 5.2 Bundled global baseline

- Global DTM at SRTM-30-equivalent resolution (~30 m).
- Global land-cover at coarse resolution (e.g., ESA WorldCover at 10 m or downsampled), paired with a system ClutterTable.
- No bundled DSM; no bundled buildings.
- The baseline is itself a single AOI Pack named `system/global-baseline`, owned by `system`, `share=shared`, read-only.
- The engine reads from this when no finer AOI Pack covers the requested geometry.

### 5.3 AOI Pack lifecycle

1. **Create.** `POST /v1/aoi-packs` with `{name, bbox, layers: {...}}`. Each layer can be:
   - **Bundled-derived:** server crops the requested bbox out of the bundled baseline at requested resolution.
   - **Fetched:** server pulls from configured upstream sources (Copernicus, OSM) for the bbox at create time only.
   - **BYO:** caller uploads via the asset model (§3.5) and references the resulting `asset_id` in the layer's `*_ref` field. Validated for CRS, extent, datatype before the pack becomes usable.
   - **Mixed:** any combination per layer.

   **Fetch failure.** If a fetched layer fails (upstream unavailable, rate-limited, partial coverage), the create call returns `207 Multi-Status` with the failed layer omitted and a warning `FETCHED_LAYER_PARTIAL`. The pack is usable at the layers that succeeded. `PATCH /v1/aoi-packs/{id}` may retry the failed layer.

   **Layer provenance.** Each fetched or BYO layer records `upstream_source` (e.g., `"copernicus_glo30"`, `"osm_buildings"`, `"byo"`), `upstream_version` (API version string when known), `acquired_at`, `content_sha256` (raster/vector bytes hash). The provenance fields populate `Run.data_layer_versions` automatically.

2. **Use.** Any analysis whose geometry intersects the pack's bbox can reference it via `aoi_ref`. If geometry crosses multiple packs, the engine picks per-pixel: highest-resolution available layer wins; falls through to baseline where no pack covers.

3. **Update.** A new AOI Pack version creates a new pack version. Old runs continue to reference the version they ran against.

4. **Delete.** Soft-delete; underlying tiles retained until garbage collection sweep when no run references them. Replay against a soft-deleted pack succeeds; replay against tiles already GC'd fails with `LAYER_GONE`.

### 5.4 Adaptive fidelity contract

The engine adapts to whatever data is present and reports the achieved tier per pixel.

| Tier | Layers used | Accuracy character |
|---|---|---|
| `T0_FREE_SPACE` | None | Sanity bound only. |
| `T1_TERRAIN` | DTM | Bare-earth diffraction. Optimistic in vegetated/urban areas. |
| `T2_TERRAIN_PLUS_CLUTTER` | DTM + clutter | Per-class attenuation. Good for sub-GHz / LoRa in vegetation. |
| `T3_SURFACE` | DSM (or DTM + canopy/building heights) | Real obstacle profile. Best for low-altitude links and dense canopy. |
| `T4_SURFACE_PLUS_BUILDINGS` | DSM + building polygons | Adds per-building penetration/wall loss. |

A tier requires *all* its prerequisite layers. The engine evaluates the achieved tier per pixel and records, on the Run:

- `fidelity_tier_dominant` — modal tier across analyzed pixels.
- `fidelity_tier_min` — worst tier reached at any analyzed pixel.
- `fidelity_tier_max` — best tier reached at any analyzed pixel.
- `fidelity_tier_max_possible` — best tier the AOI's data could theoretically support, regardless of what was used.

An optional artifact `fidelity_tier_raster` (UInt8 GeoTIFF) records the per-pixel tier for inspection (§6.1).

**Run status from fidelity.** If `fidelity_tier_dominant < fidelity_tier_max_possible`, the Run completes as `PARTIAL` with `FIDELITY_DEGRADED` (the AOI could have produced more if more data were loaded). If `fidelity_tier_dominant == fidelity_tier_max_possible`, the Run completes as `COMPLETED`.

**Fidelity floors.** Two knobs:
- `min_fidelity_tier: <tier>` — **per-pixel floor**. Every analyzed pixel must reach this tier; otherwise the run fails fast with `FIDELITY_FLOOR_NOT_MET`.
- `min_fidelity_coverage: { tier: <tier>, fraction: 0..1 }` — coverage floor. The given fraction of pixels must reach the given tier; otherwise fails with `FIDELITY_FLOOR_NOT_MET`.

### 5.5 Coordinate systems & projections

- **External API:** WGS84 lat/lon (EPSG:4326). Altitudes in meters with explicit `altitude_reference` field (`agl` or `amsl`).
- **Internal compute:** engine reprojects to a local equal-area or equidistant projection appropriate for the AOI (UTM zone for small AOIs, Lambert Azimuthal Equal-Area for large). Reprojection is automatic; not exposed in the API.
- **Output rasters:** WGS84 by default; caller may request an alternate output CRS for direct GIS handoff.
- **Resolution:** caller specifies output raster resolution explicitly in meters; engine warns (`RESOLUTION_EXCEEDS_DATA`) if requested resolution exceeds the underlying data resolution.

### 5.6 BYO data validation

- **Raster:** CRS readable, extent within declared bbox, datatype matches expected (Float32 for elevation, integer for clutter), no all-NoData. Resolution is declared.
- **Buildings vector:** valid geometry, has `height_m` attribute (or a configurable mapping), within declared bbox.
- **Clutter:** class values present in a supplied ClutterTable, or use of a known taxonomy.
- Uploads use the asset model (§3.5); validation runs at AOI Pack create/`PATCH` time, after the asset is `ready`.
- Rejected uploads return structured `BYO_LAYER_VALIDATION_FAILED` errors. Partial AOI Packs are allowed.

---

## 6. Output Artifacts & Link-Type Semantics

The output system is **client-driven shaping** — the engine emits exactly what each request declares it wants — combined with a **canonical-vs-derivative** split that controls storage cost (§8.2).

### 6.1 Universal artifacts

Artifacts are classified as **canonical** (stored to the per-class TTL) or **derivative** (generated on demand from canonicals; cached 24 h). When a run requests a derivative in `outputs[]`, it is materialized eagerly on submit; thereafter it is regenerated cheaply via `POST /v1/runs/{id}/artifacts:rederive` (§6.7) without re-running the propagation pipeline.

**Artifact key naming.** When an operation produces multiple artifacts of the same kind, the artifact key is suffixed with a dotted discriminator:

- **Per-link (Op C):** `<key>.<link_type>` — e.g., `geotiff.lora`, `link_budget.lte`, `stats.drone_c2`. Combined aggregates use `.combined` — e.g., `stats.combined`.
- **Per-Tx (Op D):** `<key>.<tx_label>` using the labels from the `best_server_raster` JSON sidecar — e.g., `geotiff.A`, `geotiff.B`.

| Artifact key | Class | Format | Content | Applicable ops |
|---|---|---|---|---|
| `link_budget` | canonical (JSON) | JSON | Full per-link breakdown: Tx power, Tx gain, cable loss, polarization mismatch (base, d, effective), free-space loss, terrain diffraction loss, clutter loss, building loss, total path loss, Rx gain, Rx feeder loss, received power, fade margin, link availability %, link result (pass/fail). **For Op A and per-link in Op C:** one object per Tx-Rx pair. **For Op B, D, E:** one Tx-side summary per Tx — component breakdown at the grid centroid; received power, fade margin, and availability reported as median / p5 / p95 across the grid (per-pixel detail lives in `geotiff` and `stats`). | A, B, C, D, E |
| `path_profile` | canonical (JSON) | JSON or GeoJSON LineString | Sampled great-circle path with terrain/surface elevation, clutter class along the path, Fresnel zone radii, line-of-sight obstruction points. | A; available on request for others |
| `geotiff` | canonical | GeoTIFF (LZW + predictor=3 by default; tiled, BIGTIFF when needed) | Single-band georeferenced raster of received signal (dBm) or path loss (dB). CRS configurable, default WGS84. | B, C, D, E (per altitude) |
| `geotiff_stack` | derivative (from `voxel`, OR canonical when `voxel` not produced) | Multi-file or multi-band GeoTIFF | One raster per altitude slice for 3D operations. | E |
| `voxel` | canonical | NetCDF (CF-conventions) with zlib level 4 + chunking; **0.5 dB quantization to UInt16 by default**, `voxel_lossless: true` keeps Float32 | Dense 3D array (lat × lon × altitude) of received signal/path loss. | E |
| `geojson_contours` | derivative (from `geotiff`) | GeoJSON FeatureCollection | Vector isolines at caller-specified thresholds. | B, C, D, E (per altitude) |
| `kmz` | derivative (from `geotiff`) | KMZ | KML overlay wrapping the raster (color-mapped) and contour lines for direct Google Earth viewing. | B, C, D, E |
| `png_with_worldfile` | derivative (from `geotiff`) | PNG + `.pgw` | Color-mapped raster image plus georeferencing sidecar. | B, C, D, E |
| `rendered_cross_section` | derivative (from `path_profile`) | PNG or SVG | Rendered terrain + Fresnel zone diagram for a path-profile result. | A |
| `stats` | canonical | JSON | AOI summary statistics: % above sensitivity threshold, mean/median margin, weakest 5th percentile margin, area covered (km²), histogram of received power. | B, C, D, E |
| `best_server_raster` | canonical | GeoTIFF (UInt8/UInt16) + JSON sidecar | Per-pixel ID of the winning Tx (categorical). Pixels at which no Tx closes the link are encoded as reserved value `0` (NoData). Tx assignments are 1-indexed in submission order. JSON sidecar maps `value → {tx_site.name, tx_equipment.name}`. **Tiebreak:** highest fade margin; on equal margin, lowest submission index. | D |
| `fidelity_tier_raster` | canonical | GeoTIFF (UInt8) | Per-pixel achieved fidelity tier index (T0=0 … T4=4). | B, C, D, E |
| `point_query` | canonical | JSON | Per-point received-signal/margin/link-result for caller-supplied locations (§6.4). | A, B, C, D, E |

### 6.2 Link-type semantic outputs

Emitted when `radio.link_type ≠ generic` and the caller opts in via the `outputs` array. All are **canonical** (JSON or GeoTIFF as appropriate).

**LoRa:**
- `lora_snr_margin` — per-pixel SNR margin in dB for the configured SF.
- `lora_best_sf` — per-pixel highest SF that closes the link (categorical 7–12). Useful for ADR planning.
- `lora_link_metrics` — JSON: time-on-air, max payload, duty-cycle compliance flags for declared region.

**LTE:**
- `lte_rsrp` — per-pixel RSRP raster.
- `lte_rsrq` — per-pixel RSRQ estimate.
- `lte_sinr` — per-pixel SINR estimate.
- `lte_mcs_feasibility` — per-pixel max feasible MCS index.

**Drone C2 (`drone_c2`):**
- `c2_pass_fail` — per-pixel pass/fail at the radio's RC sensitivity threshold.
- `c2_range_envelope` — GeoJSON polygon of the maximum operating envelope per altitude.

**RTK (`rtk`):**
- `rtk_pass_fail` — per-pixel pass/fail at correction-link sensitivity.
- `rtk_range_envelope` — GeoJSON polygon of the RTK base/relay's effective correction coverage.

### 6.3 Color mapping

Every raster derivative (KMZ, PNG, and the derived contours' color-mapped fills) supports a `color_map` parameter:

- A named built-in colormap (`viridis`, `signal_strength_default`, `pass_fail`, `lora_sf`, `lte_rsrp`, `mcs_feasibility`, etc.).
- Or an inline custom colormap: ordered list of `{value, rgba}` stops with linear interpolation between, plus an explicit `nodata` color.
- Categorical artifacts (best-server raster, best-SF map, MCS feasibility, pass/fail, fidelity tier) use indexed palettes.

Sensible per-link-type defaults are applied if no colormap is specified. Colormaps apply at derivative-generation time; they are not baked into canonicals.

### 6.4 Coordinate-resolved point queries

Every operation can return a `point_query` result: caller passes a list of `{lat, lon, alt}` and the engine returns the resolved received-signal/margin/link-result at exactly those points as JSON. Lets a caller ask "what's the signal at these 12 specific camera trap locations?" without parsing rasters.

### 6.5 Multi-link operation (Op C) aggregation

When a single site has multiple equipment profiles (e.g., LoRa + LTE + RTK + 2.4 GHz drone C2), Op C runs the pipeline once per equipment-profile and produces:

- A per-link result block (each containing whatever artifacts the caller requested for that link).
- An optional `combined_site_score` JSON with per-link pass/fail summary, weakest-link identification, and a weighted score. Default weights treat all link types equally with weakest link as the gating factor; callers may supply custom weights.

### 6.6 Voxel slicing

For Op E runs that produced a `voxel` canonical, the slice endpoint extracts subsets without downloading the full voxel:

```
GET /v1/runs/{id}/artifacts/voxel/slice
  ?bbox=south,west,north,east     # optional, default = full voxel bbox
  &altitudes=60,90,120            # discrete altitudes (m AGL), comma-separated
  &alt_min=60&alt_max=120         # OR a range (m AGL)
  &alt_step=10                    # used with range; defaults to voxel's stored step
  &format=geotiff|geotiff_stack|voxel_subset|json_point_grid
  &color_map=…                    # only for geotiff/png-style formats

Returns: artifact reference (download_url, expires_at, sha256, size_bytes),
         cached for 24 h.
```

`voxel_subset` returns a NetCDF in the same layout as the canonical voxel. `json_point_grid` returns lat/lon/alt → received-signal arrays — the canonical "what's coverage at 90 m AGL across this AOI?" answer. If a Run did not produce a `voxel` canonical, slice requests fall back to `geotiff_stack` if available, else fail with `VOXEL_NOT_AVAILABLE`.

### 6.7 Re-deriving outputs

To produce a derivative variant (different colormap, different contour thresholds, different output CRS, KMZ from an existing GeoTIFF) without re-running propagation:

```
POST /v1/runs/{id}/artifacts:rederive
  Body: {
    from: "geotiff" | "voxel" | "path_profile" | "best_server_raster",
    to:   "kmz" | "png_with_worldfile" | "geojson_contours" |
          "rendered_cross_section" | "geotiff_stack" | "geotiff",
    parameters: { color_map?, contour_levels_db?, output_crs?, … }
  }
  Response: { artifact: { key, download_url, expires_at, … }, cached_until }
```

Re-derivation operates on the persisted canonical and is bounded by the 24 h derivative cache TTL.

---

## 7. Measurements & Predicted-vs-Observed Reporting

### 7.1 Measurement Set entity

A set of observations:

```
{
  name, owner_api_key, share, version,
  ordered: bool,                    # default false; true → tracks
  site_ref (optional) | aoi_ref (optional),
  device_ref (optional),
  notes,
  points: [
    {
      lat, lon, alt_m_agl, alt_reference,
      freq_mhz,
      bandwidth_khz,
      observed_signal_dbm,
      observed_metric,     # 'rssi' | 'rsrp' | 'rsrq' | 'sinr' | 'snr'
      timestamp,
      seq,                 # required when ordered = true
      source,              # 'manual' | 'camera_trap_log' | 'drone_telemetry' |
                           # 'drive_test' | 'other'
      uncertainty_db,
      tags
    },
    ...
  ]
}
```

A measurement set may be a point cloud (camera traps that phoned home) or a track (drone flight RSSI log, drive-test). Tracks set `ordered: true` and supply `seq` per point; the engine does not interpolate.

### 7.2 Ingest

- `POST /v1/measurements` — create or full replace, with JSON body or multipart upload of CSV / GeoJSON. Request body up to 50 MB inline; larger sets upload via the asset model (§3.5) with `purpose: "measurement_csv"` and reference the asset by id.
- `POST /v1/measurements/{id}:append` — chunked append for ongoing telemetry. Body is a JSON array of points or a CSV chunk. Each chunk requires an `Idempotency-Key` header to support retries on flaky uplinks. Append creates a new Measurement Set version (light copy-on-write); point-level dedup is by `(lat, lon, alt_m_agl, freq_mhz, timestamp)`.
- Standard schemas accepted: documented CSV column convention, GeoJSON FeatureCollection with property mapping.
- Required per-point fields: `lat`, `lon`, `freq_mhz`, `observed_signal_dbm`, `timestamp`. Other fields optional.

### 7.3 Predicted-vs-observed reporting

When a Run is submitted with a `measurement_set_ref` attached (or when a Run's AOI/Site has measurement sets associated and the caller opts in), an extra reporting stage runs after analysis:

1. **Filter by frequency.** Keep points where `|observation.freq_mhz - radio.freq_mhz| ≤ freq_tolerance_mhz`. Default: `freq_tolerance_mhz = (radio.bandwidth_khz / 1000) / 2`. Per-call configurable. Filtered-out points are tagged `OBSERVATION_OUT_OF_FREQ_TOLERANCE`.

2. **Filter by metric coherence.** A point is kept only if `observed_metric` is in the set produced for the run's `link_type`:

   | link_type | accepted observed_metric |
   |---|---|
   | lora | `rssi`, `snr` |
   | lte | `rsrp`, `rsrq`, `sinr` |
   | drone_c2 | `rssi` |
   | rtk | `rssi` |
   | generic | `rssi` |

   Mismatched points are filtered out and counted with reason `OBSERVED_METRIC_MISMATCH`. Cross-metric conversion (e.g., RSSI → RSRP) is **not** performed.

3. **Filter by geometry.** Keep points whose location falls inside the analyzed geometry. Mismatched points reported with reason `OBSERVATION_OUT_OF_GEOMETRY`.

4. **Sample the prediction at each kept point** using the same point-query mechanism as §6.4.

5. **Compute per-point error.** `error_db = observed_signal_dbm - predicted_signal_dbm`. Carry through observed and predicted values, applied corrections, and any data-tier notes.

6. **Aggregate into a report block:**

```
{
  measurement_set_ref,
  n_points_filtered_in,
  n_points_filtered_out,                # total
  n_points_filtered_out_by_reason,      # map: reason → count
  mean_error_db,
  median_error_db,
  rmse_db,
  max_abs_error_db,
  bias_direction,           # 'optimistic' | 'pessimistic' | 'balanced'
  worst_5_points: [...],
  per_class_summary: [...]  # if clutter classes resolvable, error breakdown by class
}
```

Multiple measurement sets attached to one Run produce multiple report blocks (no automatic merging).

### 7.4 Explicitly deferred (post-v1)

- Automatic model calibration using measurements to bias ClutterTables or apply per-AOI offsets.
- Real-time / streaming measurement ingest.
- Cross-Run trend analysis.

---

## 8. Run Lifecycle, Retention & Non-Functional

### 8.1 Run lifecycle

```mermaid
stateDiagram-v2
    [*] --> SUBMITTED
    SUBMITTED --> QUEUED
    QUEUED --> RUNNING
    RUNNING --> COMPLETED: at AOI's max possible fidelity
    RUNNING --> PARTIAL: degraded fidelity / warnings
    RUNNING --> FAILED: unrecoverable error

    SUBMITTED --> CANCELLED: caller DELETE
    QUEUED --> CANCELLED: caller DELETE
    RUNNING --> CANCELLED: caller DELETE

    SUBMITTED --> EXPIRED: timeout
    QUEUED --> EXPIRED: timeout
    RUNNING --> EXPIRED: timeout

    COMPLETED --> [*]
    PARTIAL --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
    EXPIRED --> [*]
```

- **SUBMITTED** — orchestrator validated and persisted Run record with frozen `inputs_resolved` (timestamp recorded as `inputs_resolved_at`).
- **QUEUED** — awaiting a worker.
- **RUNNING** — a worker has claimed it; progress hints (stage name + percent) flow to the status endpoint.
- **COMPLETED** — all artifacts produced at the AOI's maximum possible fidelity.
- **PARTIAL** — artifacts produced, but at degraded fidelity (`fidelity_tier_dominant < fidelity_tier_max_possible`), with a defaulted polarization, with `FETCHED_LAYER_PARTIAL`, etc. Includes structured `warnings[]`. Treated as success for client purposes.
- **FAILED** — unrecoverable error. Includes structured `error` (Appendix D).
- **CANCELLED** — caller called `DELETE /v1/runs/{id}`. Worker cooperatively interrupts. Cancellation latency is bounded: workers check for cancellation between stages and at sub-stage checkpoints inside stages 4 (per AOI tile loaded) and 8 (per propagation batch evaluated). Worst-case cancellation latency is `cancellation_check_seconds` (default **5**). `cancellation_reason: "user"`.
- **EXPIRED** — exceeded a per-operation timeout configurable per deployment. `cancellation_reason: "expired"`.

Sync calls hold the HTTP response until COMPLETED/PARTIAL/FAILED, OR the response is auto-promoted to async at `sync_budget_seconds` (response returns `202`; the underlying Run continues running and reaches its terminal state normally). The Run record persists with the same status taxonomy regardless of how the HTTP response was shaped; the Run's `cancellation_reason` is `"sync_budget_exceeded"` only on the HTTP response, not on the Run itself.

### 8.2 Retention

Per-class TTLs replace a single flat retention. Each TTL is deployment-configurable; `pinned: true` overrides all class TTLs.

| Artifact class | Default TTL | Class | Notes |
|---|---|---|---|
| Run record (metadata) | indefinite | — | always kept |
| `link_budget`, `stats`, `point_query`, `path_profile`, link-type metric JSON | indefinite | canonical (JSON) | tiny; lives with run record |
| `geotiff` (2D) | 30 d | canonical | LZW + predictor=3 compression |
| `best_server_raster`, `fidelity_tier_raster` | 30 d | canonical | UInt8/UInt16 + LZW |
| `voxel` (canonical) | 7 d | canonical | NetCDF + zlib + 0.5 dB quantize default |
| `kmz`, `png_with_worldfile`, `geojson_contours`, `geotiff_stack`, `rendered_cross_section`, voxel slice exports | 24 h | derivative | regenerated on demand from canonicals |
| Idempotency keys | 7 d | — | re-submit window |
| Asset orphans | 7 d | — | no inbound references |

**Pinning.** `POST /v1/runs/{id}/pin` and `…/unpin` set/clear the pin flag. Pinned runs' canonical artifacts never expire; derivatives still cap at 24 h (regenerable from the pinned canonicals at any time).

**Comparison auto-pin.** A Run referenced by a Comparison/Plan is automatically pinned for as long as that Comparison exists.

**Pinned-run cap.** `max_pinned_runs` per key (default **100**) prevents runaway auto-pinning. Pin operations beyond the cap return `429 PINNED_RUN_CAP_EXCEEDED` with the oldest pin's run id surfaced for the caller to consider unpinning.

**Per-key storage quota.** `storage_quota_bytes` per key (default **10 GiB**, deployment-configurable). When exceeded, new submissions return `429 STORAGE_QUOTA_EXCEEDED` with the top 5 storage-consuming runs surfaced in the response. Set to `0` to disable the quota.

**Run-level deduplication (opt-in).** Submissions may include `dedupe: true`. If `inputs_resolved_sha256` matches a non-purged run owned by the same key, the orchestrator returns the prior run instead of creating a new one. Default `false`; intentional, so audit/verify replays remain meaningful.

**Garbage collection.** Background sweep deletes only artifacts not referenced by any pinned/Comparison-attached Run. AOI Pack tiles retained as long as any non-purged Run references the version; soft-deleted packs' tiles GC'd once no live Run references remain.

### 8.3 Reproducibility

- Every Run records `engine_version`, `engine_major`, `models_used` (with model plugin versions), `data_layer_versions` (per-layer source + version + `content_sha256`), and `inputs_resolved` (full inlined snapshot of all referenced entities; asset references appear as immutable `sha256:` content hashes).
- `POST /v1/runs/{id}/replay` resubmits the run with the same inputs against the engine version pinned in the original run's `engine_major`. The new Run record links via `replay_of_run_id`.
- Cross-major replay fails with `REPLAY_ACROSS_ENGINE_MAJOR` unless `force_replay_across_major: true` is set on the replay request. The new Run records both `replay_of_run_id` and `replay_engine_major_drift: <old → new>`.
- **Byte-identical replay** requires lossless settings (notably `voxel_lossless: true`). Otherwise replay is **semantically equivalent within the documented quantization budgets** (default 0.5 dB on voxel).

### 8.4 Auth & rate limiting

API keys are deployment-managed (rotation, revocation, per-key labels). Pluggable identity per the auth adapter contract; v1 ships only the API-key adapter.

**Auth adapter contract:**

```
AuthAdapter.authenticate(request) -> Principal | AuthError

Principal {
  tenant_id: str
  key_id: str
  scopes: list[str]            # catalog.read, catalog.write,
                               # runs.submit, runs.read, runs.write,
                               # runs.cancel, measurements.read,
                               # measurements.write, admin
  rate_limit_class: str        # selects bucket sizes
  storage_class: str           # selects storage quota
}

Permitter.permits(principal, entity, action) -> bool
```

A JWT/mTLS adapter can be added later by implementing the same interface.

**Rate limiting.** Per-key request rate (sustained + burst) and per-key concurrent run cap. `429` responses carry standard `Retry-After`.

**Quotas.** Per-key monthly compute budget (worker-seconds) and per-key storage quota (§8.2, on by default).

### 8.5 Local-mode constraints

When deployed via the bundled Docker Compose stack on a single machine:

- All services in one stack, persistent volumes mounted from host paths.
- Bundled global baseline + standard profile library + system ClutterTables seeded on first boot.
- Async runs work locally (single worker by default, scalable by adjusting compose scale).
- AOI Packs from BYO uploads work fully via the asset model; AOI Packs created via "fetch from upstream" require outbound internet at create time only.
- Asset upload/download `*_url` values point at the API service itself, which streams to/from a host-mounted volume — same client interface as the cloud deployment.
- Webhooks deliver to any reachable URL; local-only deployments may use polling instead.
- API key auth still required (operator-set on first boot); no default unauthenticated mode.

### 8.6 Observability

- **Structured JSON logs** for each request and each pipeline stage.
- **Metrics** (Prometheus-style exposition): runs by status/operation, queue depth, worker stage timings, artifact-store bytes, GC sweep stats.
- **Per-run trace** retrievable via the run record: stage timings, model selected, fidelity tier, data layers loaded, warnings.
- **Health endpoints:** `/healthz` (process liveness), `/readyz` (dependencies reachable: DB, queue, artifact store, geo data).

### 8.7 Engine version & change management

- Engine version is a single semver string stamped on every Run. The `engine_major` field is broken out for replay-compatibility checks.
- Breaking changes to model defaults, clutter taxonomies, or output formats bump the major version. Operators may pin engine version per deployment.
- Standard profile library and system ClutterTables also have versions; updates ship as additive new versions, never in-place mutations of existing ones.

### 8.8 Performance characterization

The spec does not commit to specific latency or throughput numbers — those depend on hardware and chosen technologies (decided in planning). The spec does require:

- Sync responses for point-to-point and small path-profile calls.
- Async-or-sync configurable for area, multi-link, multi-Tx, and 3D operations, with auto-promotion under `sync_budget_seconds`.
- Progress hints on long-running runs at stage granularity.
- Cancellation honored at stage and sub-stage boundaries with bounded latency (§8.1).

### 8.9 Large-data transport

The asset model (§3.5) covers caller-uploaded blobs. This subsection covers run-output downloads and on-demand re-derivation.

#### 8.9.1 Artifact references in run responses

Run responses never inline binary artifacts. The `output_artifact_refs[]` array contains entries of the shape:

```
{
  key,                        # e.g., "geotiff", "voxel", "kmz"
  class: "canonical" | "derivative",
  content_type,
  size_bytes,                 # 0 if not yet materialized
  sha256,                     # null if not yet materialized
  expires_at,                 # for derivatives, the cache horizon;
                              # for canonicals, the per-class TTL horizon
  download_url,               # presigned, 15-min TTL, supports HTTP Range
  materialized: bool,
  materialize_url             # present iff materialized = false
}
```

#### 8.9.2 Range downloads

All `download_url` responses support HTTP Range requests. Callers may stream or partially-read large artifacts (especially `voxel`, `geotiff_stack`).

#### 8.9.3 Refreshing expired URLs

```
GET /v1/runs/{id}/artifacts/{key}/url
  Response: { download_url, expires_at }
```

Returns a fresh presigned URL for the same artifact. The artifact must still exist (not GC'd, not derivative-cache-expired).

#### 8.9.4 Materializing lazy artifacts

For artifacts whose `materialized: false`:

```
POST /v1/runs/{id}/artifacts/{key}:materialize
  Body: { parameters?: { … } }     # optional, e.g., color_map for KMZ
  Response: { artifact: { key, download_url, … } }
```

Materialization runs synchronously (typically seconds for derivatives).

#### 8.9.5 Re-derivation

To produce a variant of a derivative without re-running propagation, see §6.7.

#### 8.9.6 Voxel slicing

See §6.6.

#### 8.9.7 Local-mode

In offline deployments, presigned URLs point at the API service itself, which streams from a host-mounted volume. Range requests and refresh flows behave identically.

---

## Appendix A — Operation × Output Compatibility Matrix

Legend: ✓ canonical, ✦ derivative, — not applicable.

| Output \ Op | A (P2P) | B (Area) | C (Multi-link) | D (Multi-Tx) | E (3D) |
|---|---|---|---|---|---|
| `link_budget` | ✓ | ✓ | ✓ (per link) | ✓ (per Tx-Rx) | ✓ (per altitude) |
| `path_profile` | ✓ | ✦ on request | ✦ on request | ✦ on request | ✦ on request |
| `geotiff` | — | ✓ | ✓ (per link) | ✓ (per Tx + best-server) | ✓ via slice (per altitude) |
| `geotiff_stack` | — | — | — | — | ✦ (from voxel) |
| `voxel` | — | — | — | — | ✓ |
| `geojson_contours` | — | ✦ | ✦ (per link) | ✦ | ✦ (per altitude) |
| `kmz` | — | ✦ | ✦ (per link, optionally combined) | ✦ | ✦ |
| `png_with_worldfile` | — | ✦ | ✦ | ✦ | ✦ (per altitude) |
| `rendered_cross_section` | ✦ | — | — | — | — |
| `stats` | — | ✓ | ✓ (per link + combined) | ✓ | ✓ (per altitude) |
| `best_server_raster` | — | — | — | ✓ | — |
| `fidelity_tier_raster` | — | ✓ | ✓ | ✓ | ✓ |
| `point_query` | ✓ | ✓ | ✓ | ✓ | ✓ |
| Link-type semantic outputs | ✓ | ✓ | ✓ | ✓ | ✓ |

---

## Appendix B — Frequency Band & Link-Type Coverage

| Band | Typical link types served | Native models | Fidelity tier sensitivity |
|---|---|---|---|
| 868 / 915 MHz (LoRa ISM) | LoRa | P.1812, ITM | High — clutter (T2+) materially affects forest predictions |
| 600 MHz – 3.5 GHz (LTE) | LTE | P.1812, ITM | Medium — clutter helpful, DSM helpful in urban |
| 2.4 GHz | drone_c2, rtk, generic | P.1812, ITM, P.528 (air-to-ground) | High — DSM (T3+) crucial for low-altitude links |
| 5.8 GHz | drone_c2, rtk | P.1812, ITM, P.528 | High — same as 2.4 GHz |

---

## Appendix C — Definitions

- **AGL** — Above Ground Level (height referenced to local terrain).
- **AMSL** — Above Mean Sea Level.
- **AOI** — Area of Interest.
- **Asset** — Opaque content-addressed binary blob (§3.5).
- **BYO** — Bring-Your-Own (caller-supplied) data.
- **Canonical artifact** — Persisted output, retention per class TTL (§8.2).
- **Derivative artifact** — Output regenerated on demand from canonicals; cached 24 h.
- **DTM** — Digital Terrain Model (bare-earth elevation).
- **DSM** — Digital Surface Model (terrain + canopy + buildings).
- **ITM** — Irregular Terrain Model (Longley-Rice).
- **MCS** — Modulation and Coding Scheme (LTE).
- **PvO** — Predicted-vs-Observed reporting (§7.3).
- **RSRP / RSRQ / SINR** — LTE signal-quality metrics.
- **SF** — LoRa Spreading Factor (7–12).

---

## Appendix D — Warnings, Errors, Filter Reasons

All structured codes returned via `error.code` (4xx/5xx responses or run failure), `warnings[].code` (PARTIAL completions), or per-record filter reasons.

### Errors (request rejected or run fails)

| Code | When |
|---|---|
| `RX_TX_FREQ_MISMATCH` | Rx and Tx Equipment Profile frequencies disagree by more than `tx.radio.bandwidth_khz × 1.5 / 1000` MHz. |
| `ANTENNA_OUT_OF_BAND` | Antenna used at a frequency >25 % outside its `applicable_bands`. |
| `OP_C_RX_TEMPLATE_MISSING` | Op C request has a Tx whose `link_type` has no matching `rx_template`. |
| `LINK_TYPE_NOT_REGISTERED` | A Radio Profile's `link_type` does not match any registered link-type plugin (§4.6) and is not `generic`. |
| `FIDELITY_FLOOR_NOT_MET` | `min_fidelity_tier` (per-pixel) or `min_fidelity_coverage` not satisfied. |
| `PINNED_MODEL_OUT_OF_RANGE` | Caller-pinned propagation model's frequency range does not cover the radio. |
| `PINNED_MODEL_DATA_TIER_INSUFFICIENT` | Caller-pinned propagation model requires data layers absent in the AOI. |
| `IDEMPOTENCY_KEY_BODY_MISMATCH` | Same idempotency key + same key_id + different body. |
| `LAYER_GONE` | Replay attempted against AOI Pack tiles that have been GC'd. |
| `STORAGE_QUOTA_EXCEEDED` | Per-key storage quota would be exceeded by this submission. |
| `PINNED_RUN_CAP_EXCEEDED` | Pin operation would exceed `max_pinned_runs`. |
| `AOI_OUT_OF_BBOX` | Analysis geometry outside referenced AOI Pack's bbox. |
| `BYO_LAYER_VALIDATION_FAILED` | Caller-uploaded raster/vector failed CRS / extent / datatype checks. |
| `VOXEL_NOT_AVAILABLE` | Slice request against a Run that did not produce a `voxel` canonical. |
| `REPLAY_ACROSS_ENGINE_MAJOR` | Replay would cross an engine major boundary; set `force_replay_across_major: true` to override. |

### Warnings (run succeeds, possibly PARTIAL)

| Code | When |
|---|---|
| `FIDELITY_DEGRADED` | `fidelity_tier_dominant < fidelity_tier_max_possible`. |
| `MODEL_OUT_OF_NOMINAL_FREQ` | Model used within ±10 % of its preferred frequency edge but inside its allowed range; or antenna used within the warn-band. |
| `CLUTTER_TABLE_TAXONOMY_FALLBACK` | AOI clutter taxonomy did not match the requested table; system table substituted. |
| `POLARIZATION_DEFAULTED` | Polarization missing on one end; assumed `vertical`. |
| `DSM_GAP` | DSM coverage incomplete; pixels at which DSM was missing fell back to DTM. |
| `FETCHED_LAYER_PARTIAL` | AOI Pack created with one or more upstream-fetched layers failing. |
| `RESOLUTION_EXCEEDS_DATA` | Requested output raster resolution finer than underlying data resolution. |

### Filter reasons (informational, on PvO and grid sampling)

| Code | When |
|---|---|
| `OBSERVED_METRIC_MISMATCH` | Observation's `observed_metric` not in the link_type's accepted set. |
| `OBSERVATION_OUT_OF_GEOMETRY` | Observation location outside analyzed geometry. |
| `OBSERVATION_OUT_OF_FREQ_TOLERANCE` | Observation frequency outside `freq_tolerance_mhz`. |

---

## Change log (v1 → v2)

- **§1** — added 6/60 GHz exclusion; added global conventions paragraph.
- **§2.3** — explicit auto-async thresholds; sync budget with auto-promotion to async; reference shape with `version` and no cross-key references; tightened idempotency conflict semantics.
- **§2.4** — webhook signing with `signed_at` + 5 min delta + registration challenge + secret rotation grace.
- **§2.5 (new)** — endpoint inventory.
- **§3.1** — `inputs_resolved` freeze point on SUBMITTED transition; soft-delete visibility rule; cross-key references removed.
- **§3.2** — Antenna gains `applicable_bands` / `applicable_polarizations`; Equipment Profile `cable_loss_curve` option; Measurement Set `track_ref` removed in favour of `ordered`/`seq`; `photo_ref`/`file_ref` renamed to `*_asset_ref`; ClutterTable `depolarization_factor_per_class`.
- **§3.3** — Run record gains `mode_requested`/`mode_executed`, `inputs_resolved_at`, three-tier fidelity fields, `cancellation_reason`, `inputs_resolved_sha256`, `replay_engine_major_drift`.
- **§3.5 (new)** — content-addressed Asset model with multipart upload flow.
- **§4.0** — Frequency authority subsection; Op C request shape made explicit.
- **§4.5** — replaced with concrete base-mismatch table + path-aggregated depolarization formula sourced from `ClutterTable.depolarization_factor_per_class`.
- **§5.3** — fetched-layer failure rule; per-layer provenance fields; BYO uploads via asset model.
- **§5.4** — three fidelity tier fields + `fidelity_tier_max_possible`; per-pixel `min_fidelity_tier` plus new `min_fidelity_coverage`; PARTIAL/COMPLETED status rule.
- **§6** — canonical-vs-derivative classification table; `fidelity_tier_raster` added; `best_server_raster` NoData/tiebreak rules; new §6.6 voxel slicing; new §6.7 re-derivation flow.
- **§7.2** — chunked append with idempotency; large-set ingest via asset model.
- **§7.3** — frequency filter dimensionally coherent; metric coherence table; per-reason filter counts.
- **§8.1** — cancellation latency bound; PARTIAL vs COMPLETED tied to fidelity; sync auto-promotion clarified.
- **§8.2** — per-class TTL table; canonical-vs-derivative storage; storage quota on by default; `max_pinned_runs`; opt-in `dedupe`.
- **§8.3** — engine major replay rules; byte-identical-vs-semantic replay note.
- **§8.4** — auth adapter contract specified.
- **§8.9 (new)** — large-data transport: artifact references, Range downloads, URL refresh, materialization, re-derivation, slicing, local-mode parity.
- **Appendix A** — derivative-vs-canonical legend; `fidelity_tier_raster` row added.
- **Appendix C** — Asset / canonical / derivative / PvO definitions added.
- **Appendix D (new)** — warnings, errors, filter reasons enumeration.

### v2 in-review patches (2026-04-25)

- **§1, §3.4** — vendor-specific narrative replaced with category-level framing; vendor entries (DJI Dock 2, D-RTK 3) demoted to seed-library examples built on the generic primitives. Added sensor seed examples (camera trap, fence sensor, gate sensor, wildlife collar) as Equipment Profiles; no new entity types introduced.
- **§3.2 (Radio Profile)** — `link_type` opened from a closed enum to a string; only `generic` is core. Bundled plugins ship for `lora`, `lte`, `drone_c2`, `rtk`. See §4.6.
- **§3.2 (Operating Volume)** — `Mission / Flight Envelope` renamed to `Operating Volume`; description generalized beyond drone use cases. `dock_site_ref` replaced with two optional fields `home_site_ref` (return-to-home / launch-recovery anchor) and `host_site_ref` (operational anchor for non-recovering deployments). API path `/v1/missions` → `/v1/operating-volumes`.
- **§4.6 (new)** — Link-type plugin contract parallel to §4.2 model plugin contract; declares `LINK_TYPE_NOT_REGISTERED` (Appendix D).
- **§6.2, §7.3, Appendix B** — `drtk` link-type renamed to `rtk` (RTK is the general concept; "relay" is not). Output keys `drtk_pass_fail` / `drtk_range_envelope` → `rtk_pass_fail` / `rtk_range_envelope`.
