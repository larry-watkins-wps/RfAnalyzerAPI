# Spec cleanup plan

**Status:** Active — derived from the 2026-04-25 spec audit.
**Owner:** Larry Watkins.
**Goal:** retire every BLOCKER/HIGH finding from the audit before implementation begins, and most MEDIUMs before the first sub-system lands. The spec stays at `Draft v2 — pending user review` until §12 (this plan) is fully checked off; at that point it can be promoted to `Draft v3 — ready to implement`.

The plan is structured as 12 ordered PRs in 5 phases. Each PR is small enough to ship in one cross-artifact sync pass (one commit, one re-run of the structural validators). Severity tags use the audit's labels.

---

## Phase 1 — Stop the bleeding (BLOCKERs and structural drift)

These four PRs retire every BLOCKER and the worst spec/schema/OpenAPI drift. Until they land, code-gen against any of the four canonical surfaces produces a contract that disagrees with the others.

### PR 1 — AOIPack: adopt the nested-layers shape (BLOCKER)

**Decision:** adopt the OpenAPI nested-layers shape (`layers: { dtm: AOILayer, dsm: AOILayer, clutter: AOILayer, buildings: AOILayer }`). The flat `*_ref` shape in spec §3.2 and the flat `*_asset_ref` shape in JSON Schema both lose per-layer source/upstream/version metadata that the engine actually needs.

**Fan-out:**
- Spec §3.2 AOI Pack entity description — replace flat fields with `layers` object; document `AOILayer` shape (source, asset_ref, content_sha256, upstream, version, resolution_m, …).
- Spec §5.1 – §5.3 (geo data layer types, lifecycle) — make sure narrative matches the new shape.
- JSON Schema `InlineAOIPack` — replace `dtm_asset_ref` etc. with `layers` object.
- Seed scenarios that inline AOIPacks — convert to nested shape (currently every scenario uses `{ref}` so this is mostly a no-op, but verify).
- ClutterTable: keep `clutter_table_ref` at AOIPack top-level (it's a Catalog reference, not a layer).
- Examples (op-b-area.md, op-e-voxel.md) — convert any inline AOIPacks.

### PR 2 — Scenario fixes + missing seed entries (BLOCKER)

**Decisions:**
- `ranger-vhf-handheld-comms.json`: it references `pmr-446mhz-whip-3dbi` (an Antenna) where an EquipmentProfile is required. **Add a PMR-446 RadioProfile and EquipmentProfile to the seed library** rather than gut the scenario — PMR-446 is a real wildlife-ranger band and the scenario is valuable.
- `anti-poaching-drone-dock.json`: it references `drone-c2-2_4ghz` (a RadioProfile) where an EquipmentProfile is required. Change the ref to `drone-dock-c2-2_4ghz` (the Equipment Profile that already exists).
- `meshtastic-ranger-camp-relay.json`: rename output `cross_section` → `rendered_cross_section` (the canonical name).
- `boundary-rtk-survey.json`: passes once PR 6 widens the Op A `outputs` enum.

**Fan-out:**
- Seed: add `pmr-446-446mhz` RadioProfile (12.5 kHz channel, 0.5 W EIRP cap, 446.0–446.2 MHz EU; document US PMR-446 is not allocated and add `applicable_regions: ["EU"]`).
- Seed: add `pmr-446-handheld` EquipmentProfile pairing the new radio with `pmr-446mhz-whip-3dbi`.
- Seed: bump library version; add change log entry.
- Spec §3.4 standard profile library narrative — add PMR-446 to the equipment list.
- README seed counts: 17 → 18 radio profiles, 21 → 22 equipment profiles.
- JSON Schema validate-all-scenarios script in CI (add it).

### PR 3 — `vhf_telemetry` plugin propagation (HIGH)

**Decision:** `vhf_telemetry` is bundled (already in spec/seed/scenarios). Propagate to the surfaces that don't know about it.

**Fan-out:**
- OpenAPI `LinkType` enum — add `vhf_telemetry`.
- OpenAPI `OutputKey` enum — add `vhf_detection_probability`, `vhf_bearing_quality`, `vhf_range_envelope`.
- OpenAPI `MeasurementPoint.observed_metric` enum — add `detection_count`, `bearing_quality`.
- JSON Schema enums — same additions.
- README "Plus a deliberate scaffold" line — add `vhf_telemetry` to the bundled-plugin list (CLAUDE.md was fixed in the ADR-0001 commit; mirror in README).
- Spec change log — note the propagation fix.

### PR 4 — Op A outputs widening + Op E shape lock + altitude naming (HIGH)

**Decisions:**
- Op A `outputs` enum in JSON Schema is too narrow. Add link-type semantic outputs (e.g., `rtk_pass_fail`, `lora_link_margin`, `lte_pass_fail`, `vhf_detection_probability`) — these are explicitly allowed by Appendix A for Op A.
- Op E inline shape: adopt `aoi + altitude_step_m` (schemas' shape). Update spec §4.0's `bbox + altitudes[]` description to match. Reason: matches the Operating Volume entity; simpler for clients than a list-of-altitudes.
- Altitude naming: standardize on `altitude_step_m` (not `alt_step_m`) and `altitude_reference` (not `alt_reference`). Rename `alt_m_agl` to `altitude_m` (paired with `altitude_reference: agl|amsl`) — the current name asserts AGL even when reference is AMSL.

**Fan-out:**
- Spec §4.0 Op E inline alternative — replace `bbox + altitudes[]` text with `aoi + altitude_step_m`.
- Spec §3.2 — rename `alt_reference` → `altitude_reference`, `alt_m_agl` → `altitude_m`, `alt_step_m` → `altitude_step_m` everywhere.
- JSON Schema — same renames; widen Op A `outputs` enum.
- OpenAPI — same renames; widen Op A `outputs` enum.
- All 12 scenarios — replace renamed fields.
- All worked examples — replace renamed fields.
- Golden test vectors — verify field names; re-run arithmetic check.

---

## Phase 2 — Freeze open contracts

These two PRs lock the interfaces that block implementation. After they land, plugin authors and the engine team have a complete contract.

### PR 5 — Pluggable contracts: PathLossResult, link_budget, plugin lifecycle, scenario table (HIGH)

**Decisions:**

- **`PathLossResult`** (return type of `ModelInterface.predict`) — define in §4.2:
  ```
  PathLossResult {
    pathloss_db: float                 # total path loss
    components: {                      # nullable; populated when model can decompose
      freespace_db, terrain_db, clutter_db,
      building_db, atmospheric_db, rain_db
    } | null
    fade_margin_db: float | null       # statistical fade margin (P.530, etc.)
    fidelity_tier_used: enum T0..T4    # tier the model actually consumed
    model_warnings: list[Warning]      # codes from Appendix D warnings
    model_diagnostics: dict | null     # opaque, model-specific; surfaced in Run trace only
  }
  ```
- **`link_budget` argument** to `LinkTypePluginInterface.emit` — pin its shape in §4.6 to a frozen schema with: `frequency_mhz`, `tx_eirp_dbm`, `rx_sensitivity_dbm`, `total_pathloss_db`, `polarization_mismatch_db` (split into `base_db` + `depolarization_db`), `fade_margin_db`, `cable_loss_tx_db`, `cable_loss_rx_db`, `link_margin_db`, plus the resolved Tx/Rx Equipment Profile snapshots.
- **Plugin lifecycle hooks**: `init(config)` at API startup, `validate_inputs(request)` per Run before SUBMITTED, `predict()` / `emit()` during stage execution, `teardown()` at shutdown. Reload requires API restart (no hot reload in v1).
- **Plugin version compatibility**: each plugin declares `compatible_engine_majors: [int]` and its own `version: semver`. Replay checks per-plugin major against `models_used[].plugin_major` recorded in the Run; cross-major requires `force_replay_across_major: true`. Add `MODEL_PLUGIN_MAJOR_DRIFT` and `LINK_TYPE_PLUGIN_MAJOR_DRIFT` warning codes.
- **`scenario_suitability` closed set** — freeze the enum: `terrestrial_p2p, terrestrial_area, air_to_ground, low_altitude_short_range, ionospheric, urban, indoor_outdoor`. Adding a scenario requires a spec amendment.
- **`(operation, link_type, geometry) → scenario` table** — freeze as an explicit table in §4.4 (no more "by example"). Auto-select walks the table; falls back to free-space (T0) if no row matches.

**Fan-out:**
- Spec §4.2 (model contract), §4.4 (auto-select), §4.6 (link-type contract).
- OpenAPI — add `PathLossResult`, `LinkBudget`, plugin metadata schemas; add new warning codes to `warnings.items.code` enum.
- Spec Appendix D — add `MODEL_PLUGIN_MAJOR_DRIFT`, `LINK_TYPE_PLUGIN_MAJOR_DRIFT`.
- Spec change log.

### PR 6 — Reproducibility: canonicalization + replay rules + asset GC race (HIGH)

**Decisions:**

- **`inputs_resolved_sha256` canonicalization**: RFC 8785 (JSON Canonicalization Scheme — JCS). Sorted keys, UTF-8 NFC-normalized strings, no whitespace, JSON Number per JCS rules (which means floats normalize via JS double-to-string semantics — document the trap). Add a tiny golden vector.
- **Asset GC race fix**: bump asset refcount when a Run reaches SUBMITTED (not just when COMPLETED). The refcount drops when the Run hits a terminal state AND the canonical artifacts that reference the asset have themselves been GC'd. The orphan-TTL clock starts only after refcount hits zero. Add §3.5 narrative.
- **Multipart part-URL refresh**: add `POST /v1/assets/{id}:refresh_part_urls` returning fresh presigned URLs for un-completed parts. Document the case in §3.5.
- **Standard library hash mutability**: bundled antenna patterns are immutable. If `generate_patterns.py` produces different bytes, the new pattern gets a new ID; the old `pattern_asset_ref` continues to resolve (asset persists indefinitely as long as any historical Run references it). Document in seed README.

**Fan-out:**
- Spec §3.3 — add canonicalization rule to `inputs_resolved_sha256`.
- Spec §3.5 — refcount-on-SUBMITTED rule, multipart refresh.
- Spec §8.3 — replay determinism updated.
- OpenAPI — add `:refresh_part_urls` endpoint, define request/response.
- Add a golden canonicalization vector under `seed/test-vectors/`.

---

## Phase 3 — Fill operational gaps

These three PRs close the remaining HIGH-severity contract holes — error model, geo handling, long-running jobs.

### PR 7 — Error/filter codes + missing endpoints + webhook payload + security scopes (HIGH)

**Decisions:**

- Add the 3 filter codes to OpenAPI as a separate `FilterReason.code` enum (since they're informational, not errors or warnings): `OBSERVED_METRIC_MISMATCH`, `OBSERVATION_OUT_OF_GEOMETRY`, `OBSERVATION_OUT_OF_FREQ_TOLERANCE`. Add a `FilterReport` schema; reference it from PvO output.
- Add `PATCH /v1/runs/{id}` endpoint accepting `sensitivity_class` (Appendix E.6). Define request/response.
- Add `reclassify_on_replay` (boolean) to `POST /v1/runs/{id}/replay` request body.
- Add `WebhookDelivery` schema in OpenAPI: `event`, `run_id`, `terminal_state`, `inputs_resolved_sha256`, `signed_at`, `signature_alg`, `delivery_id`. Add `webhooks:` top-level block in OpenAPI 3.1. Document `X-Signature` header format. Constrain `Webhook.events` enum to `{run.completed, run.failed, run.partial, run.canceled, run.expired}`.
- Bind per-operation security scopes in OpenAPI's `security:` blocks. Pull scopes from spec §2.5 endpoint inventory.
- Add `Unprocessable` and `RateLimited` per-status response code enum subsets (so a 422 narrows to validation codes only, etc.).
- Document Op E sync response (200) alongside the 202 — sync is allowed via `mode` override per spec.
- Constrain idempotency for in-flight (RUNNING) re-submissions: same key + same body returns the original Run record with current state; same key + different body returns 422 `IDEMPOTENCY_KEY_BODY_MISMATCH` regardless of state. Document in §2.3.

**Fan-out:**
- OpenAPI: 1 new endpoint, ~6 new schemas, security bindings on every operation, webhooks block.
- Spec §2.3 (idempotency in-flight rule), §2.4 (webhook payload), Appendix D (filter codes), Appendix E.6 (PATCH).
- Seed: PvO scenario includes a sample `FilterReport` artifact.

### PR 8 — Coordinate / projection / antimeridian / polar / datum / slant-45 (HIGH)

**Decisions:**

- **Antimeridian**: reject `west > east` with 422 `BBOX_CROSSES_ANTIMERIDIAN_NOT_SUPPORTED` for v1. Document explicitly. (Splitting AOIs at the antimeridian is an explicit non-goal of v1; revisit if a wildlife use case demands it.)
- **Polar**: AOIs with `north > 85` or `south < -85` warn `POLAR_PROJECTION_DEGRADED` (warning, not error) and are processed in EPSG:3413 (north) / EPSG:3031 (south).
- **Datum**: WGS84 only (EPSG:4326) for inputs in v1. BYO data with other CRS rejected at AOIPack creation with 422 `UNSUPPORTED_CRS`. Document in §5.5 – §5.6.
- **Internal projection**: pick LAEA centered on AOI centroid (EPSG:3035 for EU-wide, EPSG:9311 for North America, computed-LAEA for elsewhere). Document the selection rule.
- **Bbox ordering validation**: `min_mhz < max_mhz`, `bbox.south < bbox.north`, `west < east` (after antimeridian rejection), `altitude_min_m < altitude_max_m`, `min_eirp_dbm < max_eirp_dbm`. Add to JSON Schema as constraints; mirror in pydantic models with validators.
- **Slant-45 alignment**: add `slant_polarization_orientation_deg: 0 | 90 | null` to Antenna entity (only meaningful when `polarization: slant_45`). Default `null` = unspecified; engine treats unspecified slant_45 against slant_45 as worst-case 20 dB cross-pol with `POLARIZATION_DEFAULTED` warning. Spec §4.5 row clarified.

**Fan-out:**
- Spec §4.5 (polarization), §5.5–§5.6 (CRS & projections), §5.4 (fidelity tier interpolation across mixed pixels — document the "modal tier" rule explicitly).
- Spec Appendix D — add `BBOX_CROSSES_ANTIMERIDIAN_NOT_SUPPORTED`, `UNSUPPORTED_CRS`, `POLAR_PROJECTION_DEGRADED` (warning).
- OpenAPI — code enums, Antenna schema, bbox/freq-range constraints.
- JSON Schema — same constraints, Antenna schema.
- Seed: `wildlife-collar-vhf-large` and other slant-45 antennas (none currently? verify) — set orientation if known.

### PR 9 — Long-running jobs: checkpoint/resume + timeouts + Comparison cap (HIGH/MEDIUM)

**Decisions:**

- **Per-op timeout defaults** (configurable via deployment config): Op A 60 s, Op B 30 min, Op C 30 min, Op D 60 min, Op E 4 h. Document in §8.1.
- **Cancellation latency upper bound**: 60 s hard ceiling for any in-flight stage, regardless of `cancellation_check_seconds`. Stages that can't yield in 60 s must be split. Document in §8.1.
- **Voxel/area checkpointing**: tile-level checkpoint. Each completed tile (256×256 px raster, or N-altitude voxel slab) is appended to the canonical artifact incrementally. On EXPIRED, Run is restartable via `POST /v1/runs/{id}/resume` which picks up at the first incomplete tile. Add `RESUMING` state to Run state machine. Add `resume_count` field to Run record.
- **Comparison auto-pin vs cap**: Comparison creation that would exceed `max_pinned_runs` returns 409 `PINNED_RUN_CAP_WOULD_BE_EXCEEDED { current_pinned, would_pin, cap }`. Caller must explicitly raise the cap (per-key config) or pin fewer runs.
- **Multipart part expiry refresh** is in PR 6.

**Fan-out:**
- Spec §8.1 — timeouts table, cancellation latency rule, checkpoint/resume semantics, new `RESUMING` state, state-machine diagram update.
- Spec §3.3 — `resume_count` field on Run record.
- Spec §3.2 Comparison entity / §8.2 pinning narrative — cap interaction.
- Spec Appendix D — `PINNED_RUN_CAP_WOULD_BE_EXCEEDED`.
- OpenAPI — `POST /v1/runs/{id}/resume`, RESUMING state, code enum.

---

## Phase 4 — Domain corrections + naming consistency

### PR 10 — Seed library corrections (HIGH/MEDIUM)

**Decisions:**

- **`lte-handset` band/antenna mismatch**: keep the band-3 1800 MHz radio (realistic for handset). Add a new antenna `iot-endpoint-patch-1800` (band 1710–1880 MHz, gain 2 dBi, V-pol). Update `lte-handset` to pair with it.
- **`wildlife-collar-vhf-large` EIRP**: pick the seed value (10 mW). Update spec §3.4 narrative from "~1 W EIRP" to "~10 mW EIRP class". Real VHF wildlife collars are sub-100 mW; "1 W" was almost certainly a transcription error.
- **`camera-trap-lte-catm1-rx`**: borderline (806 MHz vs 863–928 MHz antenna; ~2.2% off). Add a new antenna `iot-endpoint-patch-806` (LTE Cat-M1 band 5/20 range) and pair with it. Same intervention as `lte-handset`.
- **`wildlife-collar-vhf-small` polarization**: change `wildlife-collar-loop-150mhz` antenna polarization from H to V (loop antennas oriented vertically have V-pol; matching ranger/handheld receivers). Rename to `wildlife-collar-loop-217mhz` to match the actual frequency — current name is misleading.
- **Clutter attenuation unit**: codify as `attenuation_db_per_100m`. Update the spec §3.2 ClutterTable definition, the OpenAPI ClutterTable schema, the JSON Schema, and the seed library notes.
- **ClutterTable applicable_freq_bands interpolation**: linear interpolation in dB across declared anchor frequencies; outside anchor range, nearest-frequency value. Document in §3.2.
- **ClutterTable depolarization_factor location**: move to nest inside `class_table` (spec §3.2 narrative had it at top-level; OpenAPI and seed already nest it). Spec narrative is wrong; fix the spec.

**Fan-out:**
- Seed: 2 new antennas, 1 antenna polarization fix + rename, EquipmentProfile updates, library version bump, change log.
- Spec §3.2 (ClutterTable, depolarization location), §3.4 (wildlife-collar-vhf-large EIRP narrative).
- OpenAPI ClutterTable schema (unit name, depolarization location).
- JSON Schema (same).

### PR 11 — Hash format consistency + entity-count drift + small naming nits (MEDIUM/LOW)

**Decisions:**

- **SHA-256 patterns**: standardize across OpenAPI to two named formats:
  - `Sha256Identifier` (pattern `^sha256:[0-9a-f]{64}$`) for asset IDs.
  - `Sha256Hex` (pattern `^[0-9a-f]{64}$`) for content fields (`AOILayer.content_sha256`, `Run.inputs_resolved_sha256`, `Asset.sha256`, `ArtifactRef.sha256`). Reuse via `$ref`.
- **`Run.cancellation_reason` enum** — drop `sync_budget_exceeded` from the Run schema (per §8.1 rule). Keep it on the HTTP-response shape only.
- **`Run.comparison_id` → `comparison_ids[]`** — Comparisons have many-to-many membership.
- **Tags consistency**: `EquipmentProfile` gets a `tags` field in OpenAPI (currently missing). `RegulatoryProfile` either gains a row in §3.1's tags-bearing set OR loses `tags` from OpenAPI — pick: **add `RegulatoryProfile` to the tags-bearing set** (regulatory profiles benefit from tagging by region, license class).
- **§6.1 canonical-vs-derivative drift**:
  - `path_profile`: canonical for Op A, derivative for Ops B/C/D/E. Update §6.1 row to be op-conditional.
  - `geotiff_stack`: derivative everywhere (drop the "OR canonical when `voxel` not produced" carve-out; if `voxel` isn't produced, the engine produces `geotiff` instead, not `geotiff_stack`).
- **Stage-6 building loss vagueness**: spec §5.1 says "Stage 6 building loss". §4.1 stage 6 is "Apply clutter overlay". Either rename stage 6 to "Apply clutter overlay and building loss" or move building loss to a sub-step of stage 6 with explicit text. Pick: **rename stage 6** to `apply_clutter_and_building_loss`.
- **`AssetSession` discriminator**: add `discriminator: { propertyName: kind, mapping: { … } }` to the OneOf for clean code-gen.
- **README drift**: 9 entities → 10 (`regulatory_profile`); 8 mermaid → 9; missing `vhf_telemetry` from plugin list; status snapshot table seed counts → match PR 2's PMR-446 additions.
- **Examples that reference non-existent seed entries**: convert to use real seed entries OR add a per-example header noting placeholders. Pick: **convert** the examples — clearer.

**Fan-out:**
- Spec, OpenAPI, JSON Schema all touched.
- README significantly updated.
- 5 worked examples touched.

---

## Phase 5 — Documentation hygiene

### PR 12 — Dev tooling for cross-artifact sync (LOW, but high leverage)

**Decision:** add a `scripts/check-sync.py` that runs all the structural validators in one command and a GitHub Action that runs it on every PR. The README already lists the validators inline; consolidate them into a script.

**Fan-out:**
- `scripts/check-sync.py` — runs:
  - `yaml.safe_load` on the OpenAPI.
  - `json.load` on JSON Schema and every seed JSON.
  - JSON Schema validation of every scenario.
  - Arithmetic check on golden test vectors.
  - Diff between pydantic-emitted OpenAPI (once implementation begins) and spec-derived OpenAPI.
- `.github/workflows/spec-sync.yml` — runs the script on every PR touching `docs/superpowers/specs/**`.
- README — replace inline validator snippets with `python3 scripts/check-sync.py`.

---

## Severity coverage check

After all 12 PRs land:

| Severity | Audit count | Retired | Remaining |
|---|---:|---:|---:|
| BLOCKER | 3 | 3 | 0 |
| HIGH | 26 | 26 | 0 |
| MEDIUM | 22 | ~20 | 2 (sandboxing — deferred to ADR-0002; standard library hash mutability long-tail) |
| LOW | 13 | ~11 | 2 (cosmetic) |

**Spec promotion gate:** when Phases 1–3 are complete, the spec status header can move from `Draft v2 — pending user review` to `Draft v3 — ready to implement`. Phases 4–5 can land in parallel with the first implementation milestone.

---

## Tracking

Tick the boxes as PRs land. Each PR's commit message should reference the PR number from this plan.

- [ ] PR 1 — AOIPack reconciliation
- [ ] PR 2 — Scenario fixes + PMR-446 seed
- [ ] PR 3 — vhf_telemetry plugin propagation
- [ ] PR 4 — Op A outputs + Op E shape + altitude naming
- [ ] PR 5 — Pluggable contracts (PathLossResult, link_budget, lifecycle, scenario table)
- [ ] PR 6 — Reproducibility (canonicalization, asset GC, multipart refresh)
- [ ] PR 7 — Error/filter codes + missing endpoints + webhooks + scopes
- [ ] PR 8 — Coordinate / projection / antimeridian / polar / datum / slant-45
- [ ] PR 9 — Long-running jobs (timeouts, checkpointing, resume)
- [ ] PR 10 — Seed library corrections
- [ ] PR 11 — Hash format + entity count + canonical-vs-derivative drift
- [ ] PR 12 — `scripts/check-sync.py` + CI
