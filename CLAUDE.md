# RfAnalyzer

Self-hosted, single-tenant API for RF propagation analysis used in wildlife conservation deployments. Targets field engineers placing DJI Docks, camera traps, LoRa gateways, D-RTK 3 relays, and LTE backhaul in remote/protected areas.

## Current state

**Spec design phase. No implementation code yet.** The v2 design spec is the source of truth; OpenAPI and JSON Schema are derived artifacts kept in sync with it.

## Key documents

- [Design spec v2](docs/superpowers/specs/2026-04-25-rf-site-planning-api-design.md) — authoritative behavior contract.
- [Analysis request JSON Schema](docs/superpowers/specs/2026-04-25-analysis-requests.schema.json) — JSON Schema 2020-12 for Op A–E request bodies.
- [OpenAPI 3.1](docs/superpowers/specs/2026-04-25-rf-site-planning-api.openapi.yaml) — full endpoint contract.

## Architecture

- Pluggable model registry + pipeline-stage engine, 12 stages (§4.1).
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
