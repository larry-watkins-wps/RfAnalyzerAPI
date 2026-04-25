# RfAnalyzer

Self-hosted, single-tenant API for RF propagation analysis. Targets field engineers placing autonomous drone docks, sub-GHz IoT endpoints (camera traps, fence/gate sensors, animal collars), LoRa gateways, GNSS RTK base stations, and LTE backhaul in remote/protected areas. Wildlife-protection deployments are the primary v1 driver; no spec primitive is wildlife- or vendor-specific. Vendor-specific gear (e.g., DJI Dock 2, DJI D-RTK 3) lives only as seed Equipment Profiles built on the generic catalog primitives.

## Current state

**Spec design phase. No implementation code yet.** The v2 design spec is the source of truth; OpenAPI and JSON Schema are derived artifacts kept in sync with it.

## Key documents

- [Design spec v2](docs/superpowers/specs/2026-04-25-rf-site-planning-api-design.md) — authoritative behavior contract.
- [Analysis request JSON Schema](docs/superpowers/specs/2026-04-25-analysis-requests.schema.json) — JSON Schema 2020-12 for Op A–E request bodies.
- [OpenAPI 3.1](docs/superpowers/specs/2026-04-25-rf-site-planning-api.openapi.yaml) — full endpoint contract.

## Architecture

- Pluggable propagation-model registry + pluggable link-type registry + pipeline-stage engine, 12 stages (§4.1, §4.2, §4.6). `link_type` is an open string; `generic` is core, other values come from link-type plugins (bundled: `lora`, `lte`, `drone_c2`, `rtk`).
- Five analysis ops: point-to-point, area, multi-link, multi-Tx, voxel (§4.0).
- Adaptive geo-data fidelity, five tiers from free-space to DSM+buildings (§5.4).
- Content-addressed assets (`sha256:` prefix) for binary blobs; reference-counted lifecycle (§3.5).
- Canonical-vs-derivative artifact split: canonicals persist to per-class TTL, derivatives regenerate from canonicals and cache 24 h (§6, §8.2).
- `inputs_resolved` is a frozen, fully-inlined snapshot taken at SUBMITTED — every Run is reproducible (§3.1, §8.3).

## Conventions

- All timestamps RFC 3339 UTC.
- Frequencies in MHz unless suffixed (`_khz`, `_ghz`).
- Altitudes carry an explicit `altitude_reference: "agl" | "amsl"`.
- Hashes are SHA-256, lowercase hex; `sha256:` prefix when used as identifiers.
- Cite spec sections as `§N.M`; line numbers move when the doc evolves.
- Error/warning codes in spec Appendix D mirror verbatim into OpenAPI `ProblemDetail.code`.

## Working agreements

- The spec is canonical. If JSON Schema or OpenAPI conflict with it, fix the schemas — don't change spec behavior to match.
- The status header reads `Draft v2 — pending user review`. Don't bump it without explicit instruction.
- For diagrams, prefer mermaid embedded in the spec markdown over standalone image files.
- **Cross-artifact sync is part of every spec change.** When you change a concept with a machine-readable representation — a catalog entity, an error/warning/filter code, an enum value, a pipeline stage — propagate the change across **all four surfaces in the same commit**: the spec markdown, the OpenAPI, the JSON Schema, and any affected seed (scenarios, test vectors). Re-run the structural validators (PyYAML on the OpenAPI; `json.load` on JSON files; arithmetic check on golden test vectors) before claiming complete. Drift between these surfaces silently breaks code-gen and confuses implementers; one concept commonly fans out to 10+ edit sites. The full per-change-kind checklist lives in [README.md](README.md#cross-artifact-sync--required-for-every-spec-change).
