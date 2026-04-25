# RfAnalyzer

Self-hosted, single-tenant API for RF propagation analysis used in wildlife conservation deployments. Targets field engineers placing **DJI Docks**, **camera traps**, **LoRa gateways**, **D-RTK 3 relays**, and **LTE backhaul** in remote/protected areas.

> **Status:** Spec design phase (Draft v2 — pending user review). No implementation code yet.

## What lives here

```
docs/superpowers/specs/
├── 2026-04-25-rf-site-planning-api-design.md       Spec v2 — source of truth
├── 2026-04-25-analysis-requests.schema.json        JSON Schema 2020-12 for Op A–E
├── 2026-04-25-rf-site-planning-api.openapi.yaml    OpenAPI 3.1, all endpoints
└── examples/                                       Worked request/response payloads
    ├── op-a-p2p.md
    ├── op-b-area.md
    ├── op-c-multi-link.md
    ├── op-d-multi-tx.md
    ├── op-e-voxel.md
    ├── asset-upload.md
    └── README.md
```

[`CLAUDE.md`](CLAUDE.md) at the repo root defines project context, conventions, and working agreements for AI-assisted development.

## What the API does

Five analysis operations, all flowing through one pluggable model registry + 12-stage pipeline:

| Op | Question it answers |
|---|---|
| A — point-to-point | Will this specific link close? |
| B — area heatmap | What's coverage from this Tx across this AOI? |
| C — multi-link site | Combined LoRa + LTE + drone-C2 + D-RTK coverage from one candidate dock? |
| D — multi-Tx best-server | Of these candidate sites, which dominates per pixel? |
| E — 3D / voxel | Coverage across a drone flight envelope (lat × lon × altitude)? |

Adaptive geo-data fidelity from free-space (`T0`) to DSM + buildings (`T4`); the engine reports per-pixel achieved tier and the AOI's max possible tier. Runs are reproducible via a frozen `inputs_resolved` snapshot taken at submission.

## Architecture highlights

- **Pluggable propagation models** — P.1812, ITM/Longley-Rice, P.528, P.530, P.526, free-space, two-ray — selected per call by frequency × scenario × data-tier suitability.
- **Content-addressed assets** (`sha256:` prefix) for binary blobs, with multipart upload for large rasters and idempotent re-upload.
- **Canonical-vs-derivative artifacts.** Canonicals persist to per-class TTL; derivatives (KMZ, PNG, contours, geotiff_stack, etc.) regenerate from canonicals and cache 24 h. Re-styling outputs is cheap; re-running the propagation pipeline is not.
- **Voxel slicing.** A 5 GB voxel doesn't have to be downloaded to ask "what's coverage at 90 m AGL?" — the slice endpoint returns just that.
- **Predicted-vs-observed reporting** when measurement sets are attached to a Run.
- **Single-tenant, Docker-Compose deployable**, fully offline-capable once seeded with the bundled global baseline (SRTM-30 DTM, ESA WorldCover) and standard profile library.

## Conventions

- Cite spec sections as `§N.M` — line numbers move when the doc evolves.
- Timestamps: RFC 3339 UTC. Frequencies: MHz unless suffixed. Altitudes carry an explicit `altitude_reference: "agl" | "amsl"`.
- Errors and warnings are enumerated in spec **Appendix D** and mirrored verbatim in OpenAPI `ProblemDetail.code`.
- Diagrams: mermaid, embedded in the spec markdown.

See [CLAUDE.md](CLAUDE.md) for working agreements with AI assistants.

## Status snapshot

| Layer | State |
|---|---|
| Spec | Draft v2 — pending user review |
| OpenAPI | `0.2.0-draft`, derived from spec |
| JSON Schema | Draft 2020-12, derived from spec |
| Examples | 5 op walkthroughs + asset upload |
| Implementation | Not started |
